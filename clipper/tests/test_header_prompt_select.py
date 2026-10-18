"""Header prompt style dropdown (RiceSuite #92).

The behaviour runs in ``header-prompt-ui.test.cjs`` against the real
``app.js``; these checks pin the server listing, the card markup and styles,
and the generator's frame wording.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import header_gen, jobs, main
from app.models import HeaderRequest
from tests.test_header_gen import FakeClient
from tests.test_main import _ready_job
from tests.test_render_view import _elements, _find

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "style.css").read_text(encoding="utf-8")


def test_header_prompt_ui_behaviors():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the header prompt UI behavior checks")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("header-prompt-ui.test.cjs"))],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _style(name: str, display: str) -> dict:
    return {
        "name": name,
        "display_name": display,
        "system_prompt": f"SECRET PROMPT TEXT for {name}",
        "no_topic_fallback": "fallback",
    }


def test_header_options_list_prompt_styles_and_the_default(tmp_path, monkeypatch):
    for name, display in [("alpha-header", "Alpha"), ("beta-header", "Beta")]:
        (tmp_path / f"{name}.json").write_text(json.dumps(_style(name, display)))
    (tmp_path / "broken.json").write_text("{not json")
    monkeypatch.setattr(header_gen, "PROMPTS_DIR", tmp_path)
    monkeypatch.setenv("RICECLIPPER_HEADER_STYLE", "beta-header")

    with TestClient(main.app, base_url="http://127.0.0.1:8000") as client:
        response = client.get("/api/header-options")

    data = response.json()
    assert data["prompts"] == [
        {"name": "alpha-header", "display_name": "Alpha"},
        {"name": "beta-header", "display_name": "Beta"},
    ]
    assert data["default_prompt"] == "beta-header"
    # Names only: the prompt text never leaves the server.
    assert "SECRET PROMPT TEXT" not in response.text
    assert "fallback" not in response.text


def test_header_options_default_prompt_without_env(monkeypatch):
    monkeypatch.delenv("RICECLIPPER_HEADER_STYLE", raising=False)
    with TestClient(main.app, base_url="http://127.0.0.1:8000") as client:
        data = client.get("/api/header-options").json()
    assert data["default_prompt"] == "generic-header"
    assert {"name": "generic-header", "display_name": "Generic / neutral"} in data[
        "prompts"
    ]


@pytest.fixture
def isolated_jobs(tmp_path, monkeypatch):
    root = tmp_path / ".riceclipper_work"
    root.mkdir()
    monkeypatch.setattr(jobs, "WORK_ROOT", root)
    previous_jobs = jobs._JOBS.copy()
    jobs._JOBS.clear()
    yield root
    jobs._JOBS.clear()
    jobs._JOBS.update(previous_jobs)


def test_generate_route_passes_the_chosen_style(monkeypatch, isolated_jobs):
    job = _ready_job(isolated_jobs)
    monkeypatch.setattr(main.frame, "grab_frame_b64", lambda *a, **k: "")
    captured: dict = {}

    def fake_gen(transcript, **kwargs):
        captured.update(kwargs)
        return "H"

    monkeypatch.setattr(main.header_gen, "generate_header", fake_gen)
    main.generate_header(job.id, HeaderRequest(style="beta-header"))
    assert captured["style"] == "beta-header"


def test_frame_text_asks_for_a_take_not_a_description():
    capture: list = []
    header_gen.generate_header(
        "hi", thumbnail_b64="ZmFrZQ==", client=FakeClient(capture=capture)
    )
    text = capture[0]["messages"][0]["content"][1]["text"]
    assert "ground the header in what it actually shows" not in text
    assert "not a description of it" in text


# --- card markup -----------------------------------------------------------


def test_prompt_dropdown_sits_under_generate_with_the_hint_beside_it():
    path, _, attributes = _find("header-prompt")
    assert path[-1] == "header-gen-meta"
    assert attributes.get("aria-label") == "Header prompt style"
    row = [c[0] for p, c, _ in _elements() if c and p and p[-1] == "header-gen-meta"]
    assert row == ["header-prompt", "hint"]

    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    field = html.split('<label class="header-label">Header</label>', 1)[1]
    field = field.split('<fieldset class="field choice-field content">', 1)[0]
    order = [
        field.index(marker)
        for marker in (
            'class="header-input"',
            'class="header-gen"',
            'class="header-gen-meta"',
            'class="header-gen-status',
        )
    ]
    assert order == sorted(order)
    # Until the list loads the dropdown lets the server pick.
    assert '<option value="">Default prompt</option>' in field
    assert (
        "1\u20132 lines, up to 200 characters, burned into the frame. Edit freely."
        in field
    )
    # Generation is opt-in (SPEC §6.2), so nothing is auto-filled.
    assert "Auto-filled after transcription" not in html


def test_prompt_row_wraps_at_narrow_widths():
    rule = re.search(r"\.header-gen-meta \{([^}]*)\}", CSS)
    assert rule is not None
    assert "display: flex" in rule.group(1)
    assert "flex-wrap: wrap" in rule.group(1)
    select = re.search(r"\.header-gen-meta \.header-prompt \{([^}]*)\}", CSS)
    assert select is not None and "max-width: 100%" in select.group(1)


def test_choosing_a_prompt_style_saves_it_for_the_slot_and_sends_nothing():
    listener = re.search(
        r'clip\.headerPromptEl\.addEventListener\("change", \(\) => \{(.*?)\n  \}\);',
        APP_JS,
        re.S,
    )
    assert listener is not None
    body = listener.group(1)
    assert 'rememberSlotStyle(clip.ord, "headerPrompt"' in body
    assert "fetch" not in body
    assert "requestHeader" not in body and "regenerateHeader" not in body


def test_prompt_style_is_not_a_rendered_setting():
    # The choice only steers Generate, so it must not make a render stale.
    settings = APP_JS.split("function renderSettings", 1)[1].split("\n}\n", 1)[0]
    assert "headerPrompt" not in settings
