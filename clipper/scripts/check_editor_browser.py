"""Check the live review template in Chrome at the ratified editor viewports.

Run with `python scripts/check_editor_browser.py`. Chrome and the development
requirements (including uvicorn's websockets dependency) must be installed.
Screenshots and a JSON result are written to .riceclipper_work/editor-browser/.
"""

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".riceclipper_work" / "editor-browser"
VIEWPORTS = [(390, 844), (768, 1024), (1024, 768), (1440, 900), (1920, 1080)]


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        assets = {
            "/": ("text/html", ROOT / "web/index.html"),
            "/style.css": ("text/css", ROOT / "web/style.css"),
            "/app.js": ("text/javascript", ROOT / "web/app.js"),
            "/slate-logo.png": ("image/png", ROOT / "web/slate-logo.png"),
        }
        if self.path in assets:
            kind, path = assets[self.path]
            data = path.read_bytes()
        elif self.path == "/api/health":
            kind, data = "application/json", b'{"ffmpeg":true,"libass":true}'
        elif self.path == "/api/media-info":
            kind, data = "application/json", b'{"job_dirs":0,"files":0,"total_bytes":0}'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


class DevTools:
    def __init__(self, websocket_url):
        self.socket = connect(websocket_url, origin="http://localhost", legacy=True)
        self.ident = 0
        self.errors = []

    def call(self, method, params=None):
        self.ident += 1
        ident = self.ident
        self.socket.send(
            json.dumps({"id": ident, "method": method, "params": params or {}})
        )
        while True:
            message = json.loads(self.socket.recv(timeout=20))
            if message.get("method") == "Runtime.exceptionThrown":
                self.errors.append(
                    message["params"]["exceptionDetails"].get("text", "exception")
                )
            if message.get("method") == "Runtime.consoleAPICalled":
                event = message["params"]
                if event["type"] in ("error", "assert"):
                    self.errors.append(str(event.get("args", [])))
            if message.get("id") == ident:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    def evaluate(self, source):
        result = self.call(
            "Runtime.evaluate",
            {"expression": source, "returnByValue": True, "awaitPromise": True},
        )
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result["result"].get("value")

    def close(self):
        self.socket.close()


CHECKS = r"""
(() => {
  const mode = MODE;
  const clip = window.__editorFixture || (() => {
    const item = { localId: 1, ord: 1, name: "Review fixture", status: "ready" };
    buildCard(item);
    item.geoEl.textContent = "1920x1080 — will use subject crop or blur-pad to 1080x1920.";
    item.geometryEl.hidden = false;
    const warning = item.geometryEl.querySelector(".geometry-warning");
    warning.hidden = false;
    warning.textContent = "face near header";
    item.sourceVideoEl.style.aspectRatio = "9 / 16";
    item.sourceVideoEl.style.height = "360px";
    item.transcriptEl.innerHTML = '<span class="word" contenteditable="true" tabindex="0">Correct</span> every word while timing stays locked.';
    item.lyricsInputEl.value = "Correct every word\nAlign the second line";
    document.getElementById("upload-panel").classList.add("hidden");
    document.getElementById("batch-panel").classList.remove("hidden");
    window.__editorFixture = item;
    return item;
  })();
  const radio = clip.contentEl.querySelector('[value="' + mode + '"]');
  radio.checked = true;
  radio.dispatchEvent(new Event("change", { bubbles: true }));

  const issues = [];
  const check = (truth, message) => { if (!truth) issues.push(message); };
  const rect = (el) => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom };
  };
  const near = (a, b, tolerance = 1) => Math.abs(a - b) <= tolerance;
  const css = (el) => getComputedStyle(el);
  const card = rect(clip.el);
  const grid = rect(clip.reviewGridEl);
  const preview = rect(clip.el.querySelector(".preview-col"));
  const settings = rect(clip.el.querySelector(".edit-col"));
  const settingsDocument = { ...settings, y: settings.y + scrollY, bottom: settings.bottom + scrollY };
  const transcriptPanel = rect(clip.el.querySelector(".transcript-panel"));
  const transcript = rect(clip.transcriptEl);
  const lyricsPanel = rect(clip.lyricsEl);
  const lyrics = rect(clip.lyricsInputEl);
  const action = rect(document.querySelector(".batch-actions"));
  const wide = innerWidth > 880;
  const unit = Number.parseFloat(css(document.getElementById("batch-panel")).getPropertyValue("--editor-space"));
  check(unit === 8, "editor spacing token");
  check(near(card.x, wide ? unit * 2 : unit), "page inset");
  check(near(card.right, innerWidth - (wide ? unit * 2 : unit)), "right page inset");
  check(document.documentElement.scrollWidth <= innerWidth, "horizontal document overflow");
  check(preview.x >= grid.x && settings.right <= grid.right, "clip inset");
  check(card.right <= innerWidth - (wide ? unit * 2 : unit) + 1, "clip exceeds page");
  check(action.y >= card.bottom, "action bar overlaps clip content");
  check(css(clip.sourceVideoEl).maxHeight === "360px", "video maximum height");
  check(!clip.lyricsEl.hidden === (mode === "music"), "lyric visibility");
  if (wide) {
    check(near(preview.y, settings.y), "upper row alignment");
    check(preview.right < settings.x, "upper row placement");
    check(near(preview.width / (preview.width + settings.width), 0.4, 0.025), "40/60 upper split");
    check(transcriptPanel.y >= Math.max(preview.bottom, settings.bottom), "lower row starts after upper");
    if (mode === "music") {
      check(near(transcriptPanel.width, lyricsPanel.width), "equal pane widths");
      check(near(transcript.height, lyrics.height), "equal text heights");
      check(near(transcriptPanel.y, lyricsPanel.y), "pane top alignment");
      check(near(transcript.y, lyrics.y), "text top alignment");
      check(transcriptPanel.right < lyricsPanel.x, "pane gap");
    } else {
      check(near(transcriptPanel.x, preview.x), "speech transcript left extent");
      check(near(transcriptPanel.right, settings.right), "speech transcript right extent");
    }
  } else {
    check(preview.bottom <= settings.y && settings.bottom <= transcriptPanel.y, "narrow visual order");
    if (mode === "music") check(transcriptPanel.bottom <= lyricsPanel.y, "narrow lyric order");
    check(near(transcriptPanel.width, grid.width - unit * 2), "narrow transcript width");
    if (mode === "music") check(near(lyricsPanel.width, transcriptPanel.width), "narrow lyric width");
  }
  const textProperties = ["fontFamily", "fontSize", "lineHeight", "letterSpacing", "padding", "border", "backgroundColor"];
  for (const property of textProperties) {
    check(css(clip.transcriptEl)[property] === css(clip.lyricsInputEl)[property], "matching text " + property);
  }
  check(css(clip.transcriptEl).fontFamily === "Arial, sans-serif", "Arial transcript");
  check(css(clip.transcriptEl).fontSize === "14px" && css(clip.transcriptEl).lineHeight === "21px", "reading scale");
  check(transcript.height >= 280 && transcript.height <= 440, "transcript height");
  if (mode === "music") check(lyrics.height >= 280 && lyrics.height <= 440, "lyric height");
  for (const selector of [".geo-note", ".geometry-summary", ".lyrics-badge", ".hint"]) {
    check(css(clip.el.querySelector(selector)).fontFamily === "Arial, sans-serif", "Arial operational " + selector);
  }
  check(css(clip.el.querySelector(".sample-mono")).fontFamily.includes("monospace"), "Mono sample font");
  for (const el of [clip.el.querySelector(".geometry-warning"), clip.el.querySelector(".clip-remove"), document.getElementById("restart-btn")]) {
    check(css(el).backgroundColor === "rgb(139, 0, 0)", "dark-red fill");
    check(css(el).color === "rgb(255, 255, 255)", "white control text");
  }
  for (const selector of [".clip-remove", ".choice-card", ".header-generate", ".lyrics-align", ".lyrics-restore"]) {
    const el = clip.el.querySelector(selector);
    if (el && el.getClientRects().length) check(rect(el).height >= (matchMedia("(pointer: coarse)").matches ? 44 : 36), "target size " + selector);
  }
  const focusTarget = mode === "music" ? clip.lyricsInputEl : clip.el.querySelector(".clip-remove");
  focusTarget.focus();
  check(document.activeElement === focusTarget, "keyboard focus reaches control");
  check(css(focusTarget).outlineStyle !== "none" && Number.parseFloat(css(focusTarget).outlineWidth) >= 2, "visible focus ring");
  window.scrollTo(0, document.documentElement.scrollHeight);
  const paneBottom = mode === "music" ? rect(clip.lyricsEl).bottom : rect(clip.transcriptEl).bottom;
  check(paneBottom <= rect(document.querySelector(".batch-actions")).y + 1, "action bar clears editor at page end");
  window.scrollTo(0, 0);
  return { issues, settings: settingsDocument, preview, transcriptPanel, lyricsPanel, action, scrollWidth: document.documentElement.scrollWidth, width: innerWidth, mode };
})()
"""


def browser_binary():
    configured = os.environ.get("RICECLIPPER_CHROME")
    candidates = [
        configured,
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    return next((item for item in candidates if item and Path(item).is_file()), None)


def main():
    chrome = browser_binary()
    if not chrome:
        raise SystemExit("Chrome/Chromium is required; set RICECLIPPER_CHROME")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with tempfile.TemporaryDirectory(prefix="riceclipper-chrome-") as profile:
        process = subprocess.Popen(
            [
                chrome,
                "--headless=new",
                "--no-sandbox",
                "--no-first-run",
                "--no-default-browser-check",
                "--remote-allow-origins=*",
                "--remote-debugging-port=0",
                f"--user-data-dir={profile}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            port_file = Path(profile) / "DevToolsActivePort"
            for _ in range(100):
                if port_file.exists():
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("Chrome did not expose DevTools")
            port = int(port_file.read_text().splitlines()[0])
            targets = json.load(urlopen(f"http://127.0.0.1:{port}/json/list"))
            page = next(target for target in targets if target["type"] == "page")
            devtools = DevTools(page["webSocketDebuggerUrl"])
            try:
                devtools.call("Page.enable")
                devtools.call("Runtime.enable")
                devtools.call(
                    "Page.navigate", {"url": f"http://127.0.0.1:{server.server_port}/"}
                )
                for _ in range(100):
                    if devtools.evaluate(
                        "document.readyState === 'complete' && typeof buildCard === 'function'"
                    ):
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError("editor page did not load")
                results = []
                for width, height in VIEWPORTS:
                    devtools.call(
                        "Emulation.setDeviceMetricsOverride",
                        {
                            "width": width,
                            "height": height,
                            "deviceScaleFactor": 1,
                            "mobile": False,
                        },
                    )
                    by_mode = {}
                    for mode in ("music", "speech"):
                        devtools.call(
                            "Input.dispatchKeyEvent",
                            {
                                "type": "keyDown",
                                "key": "Tab",
                                "code": "Tab",
                                "windowsVirtualKeyCode": 9,
                            },
                        )
                        devtools.call(
                            "Input.dispatchKeyEvent",
                            {
                                "type": "keyUp",
                                "key": "Tab",
                                "code": "Tab",
                                "windowsVirtualKeyCode": 9,
                            },
                        )
                        result = devtools.evaluate(
                            CHECKS.replace("MODE", json.dumps(mode), 1)
                        )
                        if result["issues"]:
                            raise AssertionError(
                                f"{width}x{height} {mode}: {result['issues']}"
                            )
                        by_mode[mode] = result
                        screenshot = devtools.call(
                            "Page.captureScreenshot",
                            {"format": "png", "captureBeyondViewport": True},
                        )
                        (OUTPUT / f"{mode}-{width}x{height}.png").write_bytes(
                            base64.b64decode(screenshot["data"])
                        )
                    music = by_mode["music"]["settings"]
                    speech = by_mode["speech"]["settings"]
                    if any(
                        abs(music[key] - speech[key]) > 1
                        for key in ("x", "y", "width", "height")
                    ):
                        raise AssertionError(
                            f"{width}x{height}: settings shifted between modes: {music} vs {speech}"
                        )
                    results.extend(by_mode.values())
                devtools.call("Emulation.setTouchEmulationEnabled", {"enabled": True})
                for width, height in VIEWPORTS[:2]:
                    devtools.call(
                        "Emulation.setDeviceMetricsOverride",
                        {
                            "width": width,
                            "height": height,
                            "deviceScaleFactor": 1,
                            "mobile": True,
                        },
                    )
                    if not devtools.evaluate('matchMedia("(pointer: coarse)").matches'):
                        raise AssertionError(
                            "touch emulation did not expose a coarse pointer"
                        )
                    for mode in ("music", "speech"):
                        devtools.call(
                            "Input.dispatchKeyEvent",
                            {
                                "type": "keyDown",
                                "key": "Tab",
                                "code": "Tab",
                                "windowsVirtualKeyCode": 9,
                            },
                        )
                        result = devtools.evaluate(
                            CHECKS.replace("MODE", json.dumps(mode), 1)
                        )
                        if result["issues"]:
                            raise AssertionError(
                                f"coarse {width}x{height} {mode}: {result['issues']}"
                            )
                if devtools.errors:
                    raise AssertionError(
                        f"browser console/JS errors: {devtools.errors}"
                    )
                (OUTPUT / "results.json").write_text(
                    json.dumps(results, indent=2) + "\n"
                )
                print(
                    f"Passed {len(results)} editor browser states plus four coarse-pointer checks; screenshots in {OUTPUT}"
                )
            finally:
                devtools.close()
        finally:
            process.terminate()
            process.wait(timeout=10)
            server.shutdown()


if __name__ == "__main__":
    main()
