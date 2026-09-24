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
    const plan = { decision: "crop", reason: "header_zone", face_rate: 1, safe_rate: 1, warning: "header_zone" };
    applyGeometry(item, { width: 1920, height: 1080, crop_plan: plan, music_plan: plan });
    item.sourceVideoEl.style.aspectRatio = "9 / 16";
    item.words = "Correct every word while timing stays locked.".split(" ").map((text, i) => ({
      text, start: i * 0.2, end: (i + 1) * 0.2, line_start: i === 0,
    }));
    renderTranscript(item);
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
  const video = rect(clip.sourceVideoEl);
  const geoNote = rect(clip.geoEl);
  const previewStatus = rect(clip.el.querySelector(".preview-status"));
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
  document.body.style.setProperty("--editor-space", "10px");
  check(near(rect(clip.el).x, wide ? 20 : 10), "page inset follows spacing token");
  check(near(rect(clip.el).right, innerWidth - (wide ? 20 : 10)), "right page inset follows spacing token");
  document.body.style.removeProperty("--editor-space");
  check(document.documentElement.scrollWidth <= innerWidth, "horizontal document overflow");
  check(preview.x >= grid.x && settings.right <= grid.right, "clip inset");
  check(card.right <= innerWidth - (wide ? unit * 2 : unit) + 1, "clip exceeds page");
  check(action.y >= card.bottom, "action bar overlaps clip content");
  check(video.height >= 360, "preview minimum video height");
  check(video.bottom <= geoNote.y && geoNote.bottom <= previewStatus.y, "notes follow video");
  check(preview.bottom - previewStatus.bottom <= unit * 2 + 2, "preview has no unused space below video and notes");
  check(css(clip.sourceVideoEl).objectFit === "contain", "video keeps its full frame");
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
  check(clip.transcriptEl.querySelector(".word").isContentEditable, "rendered transcript word remains editable");
  const words = clip.transcriptEl.querySelectorAll(".word");
  const wordGap = words[1].getBoundingClientRect().left - words[0].getBoundingClientRect().right;
  const measure = document.createElement("canvas").getContext("2d");
  measure.font = css(clip.transcriptEl).font;
  check(near(wordGap, measure.measureText(" ").width, 0.75), "generated words use ordinary text spacing");
  check(css(clip.transcriptEl).fontSize === "15px" && css(clip.transcriptEl).lineHeight === "22.5px", "reading scale");
  check(css(clip.transcriptEl).fontSize === css(clip.el.querySelector(".header-input")).fontSize, "transcript matches header text size");
  check(css(clip.lyricsInputEl).resize === "none", "lyrics cannot resize away from transcript");
  check(transcript.height >= 280 && transcript.height <= 440, "transcript height");
  if (mode === "music") check(lyrics.height >= 280 && lyrics.height <= 440, "lyric height");
  for (const selector of [".geo-note", ".geometry-summary", ".lyrics-badge", ".hint", ".preview-status", ".clip-status"]) {
    check(css(clip.el.querySelector(selector)).fontFamily === "Arial, sans-serif", "Arial operational " + selector);
  }
  check(css(document.getElementById("capbadge")).fontFamily === "Arial, sans-serif", "Arial tool status");
  check(css(clip.el.querySelector(".sample-mono")).fontFamily.includes("monospace"), "Mono sample font");
  const warning = clip.geometryEl.querySelector(".geometry-warning");
  check(!warning.hidden && warning.textContent === "face near header", "header warning remains visible and labeled");
  check(css(warning).backgroundColor === "rgb(139, 0, 0)" && css(warning).color === "rgb(255, 255, 255)", "header warning keeps solid red treatment");
  const normalButton = css(clip.el.querySelector(".header-generate"));
  for (const el of [clip.el.querySelector(".clip-remove"), document.getElementById("restart-btn")]) {
    check(css(el).backgroundColor === normalButton.backgroundColor, "red-bordered button uses normal interior");
    check(css(el).borderTopColor === "rgb(216, 65, 59)", "red-bordered button edge");
    check(css(el).color === "rgb(216, 65, 59)", "red-bordered button text matches edge");
  }
  const captionPlan = { decision: "crop", reason: "caption_zone", face_rate: 1, safe_rate: 1, warning: "caption_zone" };
  applyGeometry(clip, { width: 1920, height: 1080, crop_plan: captionPlan, music_plan: captionPlan });
  check(!warning.hidden && warning.textContent === "face near captions", "caption warning remains visible and labeled");
  check(css(warning).backgroundColor !== "rgb(139, 0, 0)", "caption warning keeps its existing treatment");
  const headerPlan = { ...captionPlan, warning: "header_zone" };
  applyGeometry(clip, { width: 1920, height: 1080, crop_plan: headerPlan, music_plan: headerPlan });
  for (const selector of [".clip-remove", ".choice-card", ".header-generate", ".lyrics-align", ".lyrics-restore", ".switch-label", ".vol"]) {
    const el = clip.el.querySelector(selector);
    if (el && el.getClientRects().length) check(rect(el).height >= (matchMedia("(pointer: coarse)").matches ? 44 : 36), "target size " + selector);
  }
  window.scrollTo(0, document.documentElement.scrollHeight);
  const paneBottom = mode === "music" ? rect(clip.lyricsEl).bottom : rect(clip.transcriptEl).bottom;
  check(paneBottom <= rect(document.querySelector(".batch-actions")).y + 1, "action bar clears editor at page end");
  window.scrollTo(0, 0);
  return { issues, settings: settingsDocument, preview, transcriptPanel, lyricsPanel, action, scrollWidth: document.documentElement.scrollWidth, width: innerWidth, mode };
})()
"""


def tab_reachability(devtools, mode, *, disabled_target=None):
    """Walk the real Tab sequence and check focus rings on representative controls."""
    if disabled_target == "lyrics":
        devtools.evaluate("window.__editorFixture.lyricsInputEl.tabIndex = -1")
    elif disabled_target == "content":
        devtools.evaluate(
            """window.__editorFixture.contentEl.querySelectorAll('input[type="radio"]').forEach((radio) => { radio.tabIndex = -1; })"""
        )
    elif disabled_target == "word":
        devtools.evaluate(
            'window.__editorFixture.transcriptEl.querySelector(".word").contentEditable = "false"'
        )
    devtools.evaluate("document.body.tabIndex = -1; document.body.focus()")
    expected = {"remove", "content", "word"}
    if mode == "music":
        expected.update({"lyrics", "align", "restore"})
    seen = set()
    for _ in range(60):
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
            {"type": "keyUp", "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9},
        )
        state = devtools.evaluate(
            """(() => {
              const c = window.__editorFixture, el = document.activeElement;
              const targets = {
                remove: c.el.querySelector(".clip-remove"),
                content: c.contentEl.querySelector('input[type="radio"]:checked'),
                word: c.transcriptEl.querySelector(".word"),
                lyrics: c.lyricsInputEl,
                align: c.lyricsAlignEl,
                restore: c.lyricsRestoreEl,
              };
              const name = Object.keys(targets).find((key) => targets[key] === el) || "";
              const style = getComputedStyle(name === "content" ? el.closest(".choice-card") : el);
              return { name, outline: style.outlineStyle, width: parseFloat(style.outlineWidth) };
            })()"""
        )
        if state["name"]:
            seen.add(state["name"])
            if state["outline"] == "none" or state["width"] < 1:
                raise AssertionError(
                    f"Tab focused {state['name']} without a visible ring"
                )
        if expected <= seen:
            break
    if disabled_target:
        if disabled_target == "lyrics":
            devtools.evaluate(
                "window.__editorFixture.lyricsInputEl.removeAttribute('tabindex')"
            )
        elif disabled_target == "content":
            devtools.evaluate(
                """window.__editorFixture.contentEl.querySelectorAll('input[type="radio"]').forEach((radio) => { radio.removeAttribute("tabindex"); })"""
            )
        else:
            devtools.evaluate(
                'window.__editorFixture.transcriptEl.querySelector(".word").contentEditable = "true"'
            )
        if disabled_target in seen:
            raise AssertionError(
                f"keyboard probe accepted {disabled_target} removed from Tab order"
            )
    elif not expected <= seen:
        raise AssertionError(
            f"{mode}: Tab missed {sorted(expected - seen)}; reached {sorted(seen)}"
        )


def content_arrow_switch(devtools, mode):
    """Check that a keyboard arrow changes the real Content mode."""
    devtools.evaluate(
        """window.__editorFixture.contentEl.querySelector('input[type="radio"]:checked').focus()"""
    )
    key = "ArrowRight" if mode == "speech" else "ArrowLeft"
    devtools.call(
        "Input.dispatchKeyEvent", {"type": "keyDown", "key": key, "code": key}
    )
    devtools.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": key})
    actual = devtools.evaluate("radioValue(window.__editorFixture.contentEl)")
    expected = "music" if mode == "speech" else "speech"
    if actual != expected:
        raise AssertionError(f"{mode} radio did not switch to {expected} with {key}")


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
                failures = []
                devtools.call(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": 1920,
                        "height": 1080,
                        "deviceScaleFactor": 1,
                        "mobile": False,
                    },
                )
                upload_width = devtools.evaluate(
                    'document.querySelector("main").getBoundingClientRect().width'
                )
                if upload_width > 1540:
                    failures.append(
                        f"upload workspace exceeded prior 1540px cap: {upload_width}"
                    )
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
                        result = devtools.evaluate(
                            CHECKS.replace("MODE", json.dumps(mode), 1)
                        )
                        if result["issues"]:
                            failures.append(
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
                        try:
                            tab_reachability(devtools, mode)
                            content_arrow_switch(devtools, mode)
                        except AssertionError as error:
                            failures.append(f"{width}x{height} {mode}: {error}")
                    music = by_mode["music"]["settings"]
                    speech = by_mode["speech"]["settings"]
                    if any(
                        abs(music[key] - speech[key]) > 1 for key in ("x", "y", "width")
                    ):
                        failures.append(
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
                            failures.append(
                                f"coarse {width}x{height} {mode}: {result['issues']}"
                            )
                devtools.evaluate(
                    """(() => {
                      const c = window.__editorFixture;
                      const radio = c.contentEl.querySelector('[value="music"]');
                      radio.checked = true;
                      radio.dispatchEvent(new Event("change", { bubbles: true }));
                    })()"""
                )
                for target in ("lyrics", "content", "word"):
                    try:
                        tab_reachability(devtools, "music", disabled_target=target)
                    except AssertionError as error:
                        failures.append(f"keyboard {target} negative control: {error}")
                output_link = devtools.evaluate(
                    """(async () => {
                      const previousFetch = window.fetch;
                      let visible = false;
                      const clip = {
                        jobId: "fixture",
                        outputUrl: null,
                        outputVideoEl: { src: "" },
                        downloadEl: { href: "", download: "" },
                        resultEl: { classList: { remove: () => { visible = true; } } },
                      };
                      let outputFetches = 0;
                      window.fetch = async (url, options) => {
                        if (String(url).endsWith("/output")) {
                          outputFetches += 1;
                          throw new TypeError("Failed to fetch");
                        }
                        return previousFetch(url, options);
                      };
                      try {
                        await showResult(clip);
                        return {
                          video: clip.outputVideoEl.src,
                          download: clip.downloadEl.href,
                          filename: clip.downloadEl.download,
                          visible,
                          blobUrl: clip.outputUrl,
                          outputFetches,
                        };
                      } finally {
                        window.fetch = previousFetch;
                      }
                    })()"""
                )
                endpoint = "/api/jobs/fixture/output"
                if output_link != {
                    "video": endpoint,
                    "download": endpoint,
                    "filename": "riceclipper-fixture.mp4",
                    "visible": True,
                    "blobUrl": None,
                    "outputFetches": 0,
                }:
                    failures.append(f"completed-render direct output: {output_link}")
                if devtools.errors:
                    failures.append(f"browser console/JS errors: {devtools.errors}")
                if failures:
                    raise AssertionError("\n".join(failures))
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
