"""Each pillar's single Anthropic call site works on the aligned SDK (1.x).

No live API call is made. Each call site runs unmodified against the real,
installed `anthropic` package; only the HTTP transport is replaced, so the
SDK's own request construction and response parsing are exercised. This is
the evidence that aligning Poster from 0.121 to 1.x needed no code change, and
that Searcher and Clipper are unaffected by the minor bump.

Each pillar runs in its own subprocess, from its own directory, because the
pillars' top-level packages would collide in one interpreter.
"""

import json
import subprocess
import sys

from ricesuite import SUITE_ROOT

# Runs before any pillar import. Two layers, so a request can never leave the
# machine: a socket guard that refuses every non-loopback connection, and a
# loopback stub of the Messages API that the SDK is pointed at through its own
# ANTHROPIC_BASE_URL setting. (Patching httpx is not enough: anthropic 1.x
# ships its own vendored HTTP client.)
_STUB = r"""
import json, os, socket, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_real_connect = socket.socket.connect
_real_getaddrinfo = socket.getaddrinfo

def _guard_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if host not in _LOOPBACK:
        raise OSError(f"network blocked in test: {address!r}")
    return _real_connect(self, address)

def _guard_getaddrinfo(host, *args, **kwargs):
    if host not in _LOOPBACK:
        raise OSError(f"DNS blocked in test: {host!r}")
    return _real_getaddrinfo(host, *args, **kwargs)

socket.socket.connect = _guard_connect
socket.getaddrinfo = _guard_getaddrinfo

CALLS = []
BODY = {
    "id": "msg_stub", "type": "message", "role": "assistant",
    "model": "stub", "stop_reason": "end_turn", "stop_sequence": None,
    "usage": {"input_tokens": 1, "output_tokens": 1},
    # Current models think by default: a reply can start with a thinking
    # block whose text is omitted (RiceSuite #66).
    "content": (
        [{"type": "thinking", "thinking": "", "signature": "sig"}]
        if THINKING_FIRST else []
    ) + [{"type": "text", "text": REPLY}],
}

class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        CALLS.append({"path": self.path, "body": json.loads(raw)})
        out = json.dumps(BODY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass

_server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
threading.Thread(target=_server.serve_forever, daemon=True).start()
os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{_server.server_port}"
"""

_CALLERS = {
    "searcher": r"""
from ricesearcher.beat.profile import BeatProfile
from ricesearcher.models import CandidateWindow
from ricesearcher.score.anthropic_scorer import AnthropicScorer
windows = [CandidateWindow(source_id="s", start=0.0, end=5.0, text="a line")]
profile = BeatProfile(version="1", name="beat", brief="brief")
scored = AnthropicScorer().score(windows, profile)
result = [[r.score, r.rationale] for r in scored]
""",
    "clipper": r"""
from app.header_gen import generate_headers
result = generate_headers("a transcript", n=1)
""",
    # The emoji picker shares the header's call site (RiceSuite #66).
    "clipper_emoji": r"""
from app.emoji_gen import suggest_emoji
from app.models import Word
words = [Word(text="pizza", start=0.0, end=0.4), Word(text="night", start=0.4, end=0.8)]
result = [[p.word, p.emoji] for p in suggest_emoji(words)]
""",
    "poster": r"""
import asyncio
from backend import captions
result = asyncio.run(captions.generate_caption("video", "a topic", "generic"))
""",
}


def _run(
    pillar: str,
    code: str,
    tmp_path,
    reply: str = "stub reply",
    thinking_first: bool = False,
) -> dict:
    script = (
        f"REPLY = {reply!r}\n"
        f"THINKING_FIRST = {thinking_first!r}\n"
        + _STUB
        + code
        + "\nprint(json.dumps({'result': result, 'calls': CALLS}))\n"
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "ANTHROPIC_API_KEY": "sk-ant-test-not-a-real-key",
        # Unroutable fallback; the stub replaces it before any pillar import.
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
        "POST_MODE": "mock",
        "RICESEARCHER_DATA_DIR": str(tmp_path / "searcher-data"),
    }
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=SUITE_ROOT / pillar,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_stub_blocks_every_non_loopback_connection(tmp_path):
    """Guard the guard: if this fails, the tests below could reach the API."""
    code = (
        "import socket\n"
        "blocked = []\n"
        "for target in [('192.0.2.1', 443), ('api.anthropic.com', 443)]:\n"
        "    try:\n"
        "        socket.create_connection(target, timeout=1)\n"
        "    except OSError as exc:\n"
        "        blocked.append('blocked in test' in str(exc))\n"
        "result = blocked\n"
    )
    assert _run("clipper", code, tmp_path)["result"] == [True, True]


def test_poster_caption_call_site_on_anthropic_1x(tmp_path):
    out = _run("poster", _CALLERS["poster"], tmp_path)
    assert out["result"] == "stub reply"
    (call,) = out["calls"]
    assert call["path"] == "/v1/messages"
    assert call["body"]["model"] == "claude-sonnet-4-6"
    assert call["body"]["max_tokens"] == 500


def test_clipper_header_call_site_on_anthropic_1x(tmp_path):
    out = _run("clipper", _CALLERS["clipper"], tmp_path)
    assert out["result"] == ["stub reply"]
    (call,) = out["calls"]
    assert call["path"] == "/v1/messages"
    assert call["body"]["model"] == "claude-sonnet-5"


def test_clipper_emoji_picker_uses_the_same_call_site(tmp_path):
    reply = '{"picks": [{"word": 0, "emoji": ["\U0001f355"]}]}'
    out = _run("clipper", _CALLERS["clipper_emoji"], tmp_path, reply=reply)
    assert out["result"] == [[0, ["\U0001f355"]]]
    (call,) = out["calls"]
    assert call["path"] == "/v1/messages"
    assert call["body"]["model"] == "claude-sonnet-5"
    # Only the phrase text goes out: no image, nothing else.
    (message,) = call["body"]["messages"]
    assert isinstance(message["content"], str)
    assert "[0] pizza [1] night" in message["content"]


def test_searcher_scorer_call_site_on_anthropic_1x(tmp_path):
    reply = '[{"index": 0, "score": 0.75, "rationale": "clear hook"}]'
    out = _run("searcher", _CALLERS["searcher"], tmp_path, reply=reply)
    assert out["result"] == [[0.75, "clear hook"]]
    (call,) = out["calls"]
    assert call["path"] == "/v1/messages"
    assert call["body"]["model"] == "claude-haiku-4-5"
    assert call["body"]["max_tokens"] == 2048


def test_clipper_header_reads_a_thinking_first_reply(tmp_path):
    out = _run("clipper", _CALLERS["clipper"], tmp_path, thinking_first=True)
    assert out["result"] == ["stub reply"]


def test_clipper_emoji_picker_reads_a_thinking_first_reply(tmp_path):
    reply = '{"picks": [{"word": 0, "emoji": ["\U0001f355"]}]}'
    out = _run(
        "clipper", _CALLERS["clipper_emoji"], tmp_path, reply=reply, thinking_first=True
    )
    assert out["result"] == [[0, ["\U0001f355"]]]
