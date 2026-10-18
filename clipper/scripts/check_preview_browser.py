"""Exercise composed previews with synthetic media and one temporary Chrome profile.

Run `python scripts/check_preview_browser.py`; writes screenshots/results under
system temp only. Requires ffmpeg, Chrome and the development dependencies.
"""

import base64
import json
import shutil
import subprocess
import tempfile
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

from check_editor_browser import VIEWPORTS, DevTools, FixtureHandler, browser_binary

OUTPUT = Path(tempfile.gettempdir()) / "riceclipper-preview-browser"

SETUP = r"""
(async () => {
  const c = { localId: 1, ord: 1, name: "Synthetic preview", status: "done", words: [] };
  document.getElementById("upload-panel").classList.add("hidden");
  document.getElementById("batch-panel").classList.remove("hidden");
  buildCard(c); clips.push(c); window.c = c;
  c.headerEl.value = "A header to check in fullscreen";
  const canvas = document.createElement("canvas"); canvas.width = 1080; canvas.height = 1920;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "white"; ctx.font = "bold 52px Arial"; ctx.textAlign = "center";
  ctx.fillText(c.headerEl.value, 540, 280); window.headerPNG = canvas.toDataURL();
  window.loadMedia = async (media, url) => {
    const loaded = new Promise((resolve, reject) => {
      media.onloadeddata = resolve; media.onerror = () => reject(Error("fixture failed"));
    });
    media.src = url; await loaded;
  };
  await loadMedia(c.outputVideoEl, mediaURLs.portrait);
  c.resultEl.classList.remove("is-empty"); syncPreviewFullscreen(c);
  return document.getElementById("tiktok-preview-toggle").checked;
})()
"""

CHECK = r"""
(async () => {
  const issues = [];
  const check = (on, message) => { if (!on) issues.push(message); };
  const rect = (el) => el.getBoundingClientRect();
  const visible = (el) => !el.hidden && getComputedStyle(el).display !== "none";
  const variant = VARIANT;
  const plan = { decision: variant === "crop" ? "crop" : "blur_pad", face_rate: 1, safe_rate: 1 };
  c.isPhoto = variant === "photo";
  c.el.classList.toggle("photo-card", c.isPhoto);
  if (c.isPhoto) {
    const loaded = new Promise((resolve) => { c.sourcePhotoEl.onload = resolve; });
    c.sourcePhotoEl.src = photoURL; await loaded;
  } else {
    await loadMedia(c.sourceVideoEl, mediaURLs[variant === "crop" ? "landscape" : variant === "blur" ? "square" : "portrait"]);
    c.sourceVideoEl.pause(); c.sourceVideoEl.currentTime = 0.5;
    await new Promise((resolve) => c.sourceVideoEl.addEventListener("seeked", resolve, { once: true }));
  }
  const media = c.isPhoto ? c.sourcePhotoEl : c.sourceVideoEl;
  c.geoState = { width: media.videoWidth || media.naturalWidth,
    height: media.videoHeight || media.naturalHeight, crop_plan: plan, music_plan: plan };
  applyGeometry(c, c.geoState);
  showHeaderPreview(c, headerPNG);
  const fingerprint = renderFingerprint(c); const snapshot = batchSnapshot();
  const toggle = document.getElementById("tiktok-preview-toggle");
  toggle.checked = true; toggle.dispatchEvent(new Event("change", { bubbles: true }));
  await new Promise(requestAnimationFrame);
  check(renderFingerprint(c) === fingerprint && batchSnapshot() === snapshot, "toggle changed render/send state");
  check(c.el.querySelectorAll(".tiktok-overlay").length === 2, "two overlays");
  for (const el of c.el.querySelectorAll(".tiktok-overlay")) {
    check(visible(el) && el.complete && el.naturalWidth === 1080, "local overlay visible/loaded");
    check(getComputedStyle(el).pointerEvents === "none" && el.getAttribute("aria-hidden") === "true", "decorative overlay");
  }
  window.checkComposition = () => {
    const r = rect(c.headerPreviewWindowEl), m = rect(media);
    check(Math.abs(r.width / r.height - 9 / 16) < 0.001, "source output frame ratio");
    check(r.left >= m.left - 2 && r.right <= m.right + 2 && r.top >= m.top - 2 && r.bottom <= m.bottom + 2, "source frame inside media");
    check(Math.abs(rect(c.headerPreviewEl).width - r.width) < 1, "header aligns with overlay");
    check(c.headerPreviewBaseEl.hidden === !["blur", "photo"].includes(variant), "blur-pad mock state");
    check(!c.headerPreviewWindowEl.hidden && !c.headerPreviewEl.hidden, "header visible");
  };
  checkComposition();
  check(document.documentElement.scrollWidth <= innerWidth, "no document overflow");
  for (const video of [c.sourceVideoEl, c.outputVideoEl]) {
    check(video.controls, "original browser controls are enabled");
  }
  check(!c.el.querySelector(".preview-seek"), "no custom transport bar");
  window.previewIssues = issues;
  return issues;
})()
"""


def evaluate(devtools, source, *, gesture=False):
    result = devtools.call(
        "Runtime.evaluate",
        {
            "expression": source,
            "returnByValue": True,
            "awaitPromise": True,
            "userGesture": gesture,
        },
    )
    if "exceptionDetails" in result:
        raise RuntimeError(result["exceptionDetails"])
    return result["result"].get("value")


def screenshot(devtools, name):
    data = devtools.call("Page.captureScreenshot", {"format": "png"})
    (OUTPUT / name).write_bytes(base64.b64decode(data["data"]))


def check_native_playback(devtools, media, screenshot_name):
    evaluate(devtools, f"{media}.pause()")
    point = evaluate(
        devtools,
        f"(() => {{ const r={media}.getBoundingClientRect(); return {{x:r.left+30,y:r.bottom-30}}; }})()",
    )
    devtools.call("Input.dispatchMouseEvent", {"type": "mouseMoved", **point})
    time.sleep(0.15)
    screenshot(devtools, screenshot_name)
    before = evaluate(
        devtools, "({fingerprint:renderFingerprint(c),snapshot:batchSnapshot()})"
    )
    for kind in ("mousePressed", "mouseReleased"):
        devtools.call(
            "Input.dispatchMouseEvent",
            {"type": kind, **point, "button": "left", "clickCount": 1},
        )
    for _ in range(20):
        if evaluate(devtools, f"!{media}.paused"):
            break
        time.sleep(0.05)
    assert evaluate(devtools, f"!{media}.paused"), (
        "native Play receives real pointer input with overlay enabled"
    )
    for kind in ("mousePressed", "mouseReleased"):
        devtools.call(
            "Input.dispatchMouseEvent",
            {"type": kind, **point, "button": "left", "clickCount": 1},
        )
    assert evaluate(devtools, f"{media}.paused"), (
        "native Pause receives real pointer input"
    )
    after = evaluate(
        devtools, "({fingerprint:renderFingerprint(c),snapshot:batchSnapshot()})"
    )
    assert before == after, "native controls never mark a clip edited"


def main():
    chrome, ffmpeg = browser_binary(), shutil.which("ffmpeg")
    if not chrome or not ffmpeg:
        raise SystemExit("Chrome and ffmpeg are required")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory(prefix="clipper-preview-") as directory:
        temp = Path(directory)
        media = {}
        for name, size in (
            ("portrait", "1080x1920"),
            ("landscape", "1920x1080"),
            ("square", "720x720"),
        ):
            file = temp / f"{name}.mp4"
            subprocess.run(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=0x405670:s={size}:r=10",
                    "-t",
                    "2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-an",
                    str(file),
                ],
                check=True,
            )
            media[name] = (
                "data:video/mp4;base64," + base64.b64encode(file.read_bytes()).decode()
            )
        process = subprocess.Popen(
            [
                chrome,
                "--headless=new",
                "--no-sandbox",
                "--use-mock-keychain",
                "--no-first-run",
                "--no-default-browser-check",
                "--remote-allow-origins=*",
                "--remote-debugging-port=0",
                f"--user-data-dir={temp / 'profile'}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        devtools = None
        try:
            port_file = temp / "profile/DevToolsActivePort"
            for _ in range(100):
                if port_file.exists():
                    break
                time.sleep(0.1)
            port = int(port_file.read_text().splitlines()[0])
            pages = json.load(urlopen(f"http://127.0.0.1:{port}/json/list"))
            devtools = DevTools(
                next(p["webSocketDebuggerUrl"] for p in pages if p["type"] == "page")
            )
            devtools.call("Page.enable")
            devtools.call("Runtime.enable")
            devtools.call(
                "Page.navigate", {"url": f"http://127.0.0.1:{server.server_port}/"}
            )
            for _ in range(100):
                if evaluate(
                    devtools,
                    "document.readyState === 'complete' && typeof buildCard === 'function'",
                ):
                    break
                time.sleep(0.1)
            evaluate(devtools, f"window.mediaURLs = {json.dumps(media)}")
            evaluate(
                devtools,
                'window.photoURL = \'data:image/svg+xml,\' + encodeURIComponent(\'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="600"><rect width="900" height="600" fill="#405670"/></svg>\')',
            )
            assert evaluate(devtools, SETUP) is False, (
                "fresh browser starts overlay off"
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
                for variant in ("portrait", "crop", "blur", "photo"):
                    issues = evaluate(
                        devtools, CHECK.replace("VARIANT", json.dumps(variant), 1)
                    )
                    assert not issues, (width, height, variant, issues)
                    stage = "c.previewStages[0]"
                    evaluate(
                        devtools, f"togglePreviewFullscreen(c, {stage})", gesture=True
                    )
                    assert evaluate(
                        devtools, f"document.fullscreenElement === {stage}"
                    ), "source entered fullscreen"
                    time.sleep(0.05)
                    issues = evaluate(devtools, "checkComposition(); previewIssues")
                    assert not issues, (width, height, variant, "fullscreen", issues)
                    if (width, variant) == (1440, "portrait"):
                        check_native_playback(
                            devtools, "c.sourceVideoEl", "source-fullscreen.png"
                        )
                    # Browser-level Escape exits without the button handler.
                    devtools.call(
                        "Input.dispatchKeyEvent",
                        {
                            "type": "keyDown",
                            "key": "Escape",
                            "code": "Escape",
                            "windowsVirtualKeyCode": 27,
                        },
                    )
                    if evaluate(devtools, "Boolean(document.fullscreenElement)"):
                        evaluate(
                            devtools,
                            f"togglePreviewFullscreen(c, {stage})",
                            gesture=True,
                        )
                    for _ in range(20):
                        if evaluate(
                            devtools,
                            f"!document.fullscreenElement && document.activeElement === {stage}.querySelector('.preview-fullscreen')",
                        ):
                            break
                        time.sleep(0.05)
                    assert evaluate(
                        devtools,
                        f"!document.fullscreenElement && document.activeElement === {stage}.querySelector('.preview-fullscreen')",
                    ), "exit focus"
                    results.append(
                        {
                            "viewport": [width, height],
                            "variant": variant,
                            "fullscreen": True,
                        }
                    )
                evaluate(
                    devtools,
                    "togglePreviewFullscreen(c, c.previewStages[1])",
                    gesture=True,
                )
                assert evaluate(
                    devtools, "document.fullscreenElement === c.previewStages[1]"
                ), "rendered fullscreen"
                time.sleep(0.05)
                assert evaluate(
                    devtools,
                    "(() => { const r=c.outputVideoEl.getBoundingClientRect(); return Math.abs(r.width/r.height-9/16)<.001 && r.bottom<innerHeight && r.width>100; })()",
                ), "fullscreen rendered fits 9:16"
                if width == 1440:
                    check_native_playback(
                        devtools, "c.outputVideoEl", "rendered-fullscreen.png"
                    )
                evaluate(
                    devtools,
                    "togglePreviewFullscreen(c, c.previewStages[1])",
                    gesture=True,
                )
            # Both rendered placeholder states hide the decorative overlay.
            for state in ("is-empty", "is-busy"):
                assert evaluate(
                    devtools,
                    f"c.resultEl.classList.add('{state}'); syncPreviewFullscreen(c); getComputedStyle(c.resultEl.querySelector('.tiktok-overlay')).display === 'none' && c.previewStages[1].querySelector('button').disabled",
                ), state
                evaluate(devtools, f"c.resultEl.classList.remove('{state}')")
            evaluate(devtools, CHECK.replace("VARIANT", '"portrait"', 1))
            screenshot(devtools, "inline.png")
            # The suite embeds Clipper in a same-origin iframe. A real pointer
            # click gives that child a user gesture, just as the fullscreen button
            # does in daily use (creating a frame after a gesture does not).
            evaluate(
                devtools,
                """(async () => {
                  const frame = document.createElement("iframe");
                  frame.src = location.href; frame.title = "Clip fixture";
                  frame.style.cssText = "position:fixed;inset:0;width:100%;height:100%;z-index:99;border:0";
                  const loaded = new Promise((resolve) => { frame.onload = resolve; });
                  document.body.append(frame); await loaded;
                  window.fixtureFrame = frame;
                  const child = frame.contentWindow;
                  child.mediaURLs = mediaURLs;
                  await child.eval(CHILD_SETUP);
                  await child.loadMedia(child.c.sourceVideoEl, mediaURLs.portrait);
                  child.c.geoState = { width: 1080, height: 1920 };
                  child.showHeaderPreview(child.c, child.headerPNG);
                  child.setTikTokPreview(true);
                })()""".replace("CHILD_SETUP", json.dumps(SETUP)),
            )
            point = evaluate(
                devtools,
                """(() => {
                  const button = fixtureFrame.contentWindow.c.previewStages[0].querySelector('.preview-fullscreen');
                  button.scrollIntoView({ block: "center" });
                  const r = button.getBoundingClientRect(), f = fixtureFrame.getBoundingClientRect();
                  return { x: f.x + r.x + r.width / 2, y: f.y + r.y + r.height / 2 };
                })()""",
            )
            for kind in ("mousePressed", "mouseReleased"):
                devtools.call(
                    "Input.dispatchMouseEvent",
                    {"type": kind, **point, "button": "left", "clickCount": 1},
                )
            for _ in range(20):
                embedded = evaluate(
                    devtools,
                    "fixtureFrame.contentDocument.fullscreenElement === fixtureFrame.contentWindow.c.previewStages[0]",
                )
                if embedded:
                    break
                time.sleep(0.05)
            assert embedded, "composed fullscreen works through the suite's iframe"
            evaluate(
                devtools,
                "(async () => { await fixtureFrame.contentDocument.exitFullscreen(); fixtureFrame.remove(); })()",
            )
            # Recreate the document while preserving this browser's localStorage.
            devtools.call("Page.reload")
            for _ in range(100):
                if evaluate(
                    devtools,
                    "document.readyState === 'complete' && typeof tiktokPreviewToggle !== 'undefined' && tiktokPreviewToggle.checked",
                ):
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("overlay preference not restored after reload")
            assert not devtools.errors, devtools.errors
            (OUTPUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            print(
                f"Passed {len(results)} source states and 7 rendered fullscreen states; screenshots: {OUTPUT}"
            )
        finally:
            if devtools:
                devtools.close()
            process.terminate()
            process.wait(timeout=10)
            server.shutdown()


if __name__ == "__main__":
    main()
