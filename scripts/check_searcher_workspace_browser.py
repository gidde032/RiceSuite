"""Required production Searcher browser contracts. All requests use fixtures.

No library, account, credentials, models, downloads or other pillars are opened.
The checked-in synthetic MP4 exercises real native playback and seeking.
"""

# Browser expressions stay together for readable assertion diagnostics.
# ruff: noqa: E501
from __future__ import annotations

import argparse
import json
import mimetypes
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "searcher/ricesearcher/web/static"
VIEWPORTS = [
    (320, 800),
    (390, 844),
    (768, 1024),
    (899, 800),
    (900, 800),
    (1024, 768),
    (1366, 768),
    (1440, 900),
    (1920, 1080),
]
PROFILES = [
    {"id": "fixture", "name": "Fixture profile", "version": "1", "selected": 2},
    {"id": "other", "name": "Other", "version": "1", "selected": 0},
]
SOURCES = [
    {
        "id": "source",
        "title": "Long title " * 30,
        "ref": "/synthetic/source.mp4",
        "kind": "local",
        "channel": "",
        "media_url": "cache/source.mp4",
        "size_bytes": 1000,
        "duration_s": 12,
        "acquired_at": "2026-10-22",
        "slice_count": 4,
    },
    {
        "id": "missing",
        "title": "Missing media",
        "ref": "fixture",
        "kind": "local",
        "media_url": None,
        "size_bytes": None,
        "duration_s": 12,
        "slice_count": 0,
    },
]
SLICES = [
    {
        "id": f"slice-{n}",
        "source_id": "source" if n < 4 else "missing",
        "source_title": "Long title " * 30,
        "profile_id": "fixture",
        "status": "selected" if n < 2 else "handed_off" if n == 4 else "candidate",
        "origin": "manual" if n == 0 else "scored",
        "score": None if n == 0 else 0.8 - n * 0.01,
        "heuristic_score": None if n == 0 else 0.5,
        "scorer_model": "" if n == 0 else "heuristic-offline",
        "media_url": "cache/source.mp4",
        "target_in": 1,
        "target_out": 4,
        "rights_risk": "low",
        "rationale": "Fixture rationale",
        "transcript_span": "Full transcript " * 30,
        "stale": n != 0,
        "dup_of": "canonical" if n == 2 else None,
        "dup_label": "Possible fixture duplicate",
        "dup_kind": "overlap",
        "dup_score": 0.9,
        "created_at": f"2026-10-22T00:00:0{n}Z",
    }
    for n in range(5)
]


def attach(page, scenario="normal"):
    errors, mutations = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: (
            errors.append(message.text) if message.type == "error" else None
        ),
    )
    saved = []
    run_state = {"run": None}

    def route(request):
        url = urlsplit(request.request.url)
        path = url.path
        query = parse_qs(url.query)
        if "/api/" in path:
            if request.request.method != "GET":
                mutations.append((path, request.request.post_data))
            if path.endswith("/profiles"):
                request.fulfill(json=PROFILES)
            elif path.endswith("/settings"):
                request.fulfill(
                    json={
                        "scoring_available": False,
                        "transcription_model": "small",
                        "threshold": 0.65,
                    }
                )
            elif path.endswith("/runs/current"):
                request.fulfill(json=run_state)
            elif path.endswith("/runs") and request.request.method == "POST":
                data = json.loads(request.request.post_data)
                run_state["run"] = {
                    "id": data["attempt_key"],
                    "profile": data["profile"],
                    "status": "active",
                    "stage": "scoring",
                    "stop_requested": False,
                    "detail": "",
                    "duplicate_error": "",
                    "items": [
                        {
                            "request": "Synthetic source",
                            "source_id": "source",
                            "state": "active",
                            "stage": "scoring",
                            "error": "",
                        }
                    ],
                }
                if scenario == "response-loss":
                    request.abort()
                else:
                    request.fulfill(json=run_state["run"])
            elif "/runs/" in path and run_state["run"]:
                if path.endswith("/stop"):
                    run_state["run"]["stop_requested"] = True
                request.fulfill(json=run_state["run"])
            elif path.endswith("/manual"):
                manual = {
                    **SLICES[0],
                    "id": "saved-manual",
                    "status": "reviewed",
                    "created_at": "2026-10-22T00:10:00Z",
                }
                saved.append(manual)
                request.fulfill(json=manual)
            elif path.endswith("/sources"):
                request.fulfill(json=[] if scenario == "empty" else SOURCES)
            elif path.endswith("/sources/source") or path.endswith("/sources/missing"):
                request.fulfill(
                    status=503 if scenario == "source-error" else 200,
                    json={
                        "detail": "Synthetic source read failure",
                        "transcript": "Long transcript text " * 1000,
                        "transcription_state": "complete",
                        "words": [],
                    },
                )
            elif path.endswith("/slices"):
                rows = (
                    []
                    if scenario == "empty" or query.get("profile", [""])[0] == "other"
                    else SLICES + saved
                )
                rows = [
                    s
                    for s in rows
                    if all(
                        not query.get(k) or s[field] == query[k][0]
                        for k, field in [
                            ("source", "source_id"),
                            ("kind", "origin"),
                            ("status", "status"),
                        ]
                    )
                ]
                request.fulfill(json=rows)
            else:
                request.fulfill(status=404, json={"detail": "fixture unavailable"})
            return
        if path.endswith("source.mp4"):
            data = (ROOT / "tests/fixtures/workspace/source.mp4").read_bytes()
            request.fulfill(
                body=data, content_type="video/mp4", headers={"Accept-Ranges": "bytes"}
            )
            return
        name = path.rsplit("/", 1)[-1]
        file = STATIC / (
            "media.html"
            if name in {"sources", "media"}
            else "index.html"
            if not name
            else name
        )
        if name == "slate.css":
            file = ROOT / "ricesuite/shell/slate.css"
        if not file.is_file():
            request.fulfill(status=404, body="fixture unavailable")
            return
        request.fulfill(
            body=file.read_bytes(),
            content_type=mimetypes.guess_type(file.name)[0]
            or "application/octet-stream",
        )

    page.route("**/*", route)
    return errors, mutations


def assert_no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
        page.evaluate(
            "({width:innerWidth, scroll:document.documentElement.scrollWidth})"
        )
    )


def check(output: Path, channel: str | None = None):
    output.mkdir(parents=True, exist_ok=True)
    result = {"viewports": [], "scenarios": [], "screenshots": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel=channel)
        try:
            for width, height in VIEWPORTS:
                page = browser.new_page(viewport={"width": width, "height": height})
                errors, mutations = attach(page)
                page.goto("http://127.0.0.1:8966/sources")
                page.get_by_role("button", name="Open source").first.wait_for()
                assert_no_overflow(page)
                assert page.locator("#sourceInspector").is_visible() == (width >= 900)
                page.get_by_role("button", name="Open source").first.click()
                page.locator("#fullSourceVideo").wait_for()
                assert page.locator("#sourceLibrary").is_visible() == (width >= 900)
                geometry = page.evaluate("""() => {
                  const work=document.querySelector('.source-workspace'), lib=document.querySelector('#sourceLibrary'), player=document.querySelector('.source-player'), video=document.querySelector('video'), form=document.querySelector('.segment-form');
                  const css=getComputedStyle(work), v=getComputedStyle(video);
                  return {inset:parseFloat(css.paddingLeft),gap:parseFloat(css.columnGap),library:lib.getBoundingClientRect().width,player:player.getBoundingClientRect().toJSON(),form:form.getBoundingClientRect().toJSON(),fit:v.objectFit,body:getComputedStyle(document.body).fontSize,font:getComputedStyle(document.querySelector('.source-transcript')).fontFamily,controls:video.controls};
                }""")
                assert geometry["inset"] == (16 if width >= 900 else 8)
                assert (
                    geometry["gap"] == 16
                    and geometry["fit"] == "contain"
                    and geometry["controls"]
                )
                assert geometry["body"] == "15px" and geometry["font"].startswith(
                    "Arial"
                )
                assert geometry["player"]["height"] <= min(height * 0.56, 480) + 1
                assert geometry["form"]["y"] >= geometry["player"]["bottom"]
                if width >= 900:
                    assert 240 <= geometry["library"] <= 360
                assert_no_overflow(page)
                page.evaluate(
                    "document.documentElement.style.setProperty('--searcher-space','12px')"
                )
                assert page.locator(".source-workspace").evaluate(
                    "e => parseFloat(getComputedStyle(e).paddingLeft)"
                ) == (24 if width >= 900 else 12)
                assert (
                    page.locator(".source-workspace").evaluate(
                        "e => parseFloat(getComputedStyle(e).columnGap)"
                    )
                    == 24
                )
                page.evaluate(
                    "document.documentElement.style.removeProperty('--searcher-space')"
                )
                if width in {390, 1366}:
                    file = output / f"sources-{width}.png"
                    page.screenshot(path=str(file), full_page=True)
                    result["screenshots"].append(str(file))
                if width < 900:
                    page.get_by_role("button", name="Back to sources").click()
                    assert (
                        page.locator("#sourceLibrary").is_visible()
                        and not page.locator("#sourceInspector").is_visible()
                    )
                    page.get_by_role("button", name="Open source").first.click()
                if width == 1366:
                    page.wait_for_function(
                        "document.querySelector('video').readyState >= 2"
                    )
                    page.locator("video").evaluate(
                        "async v => {v.muted=true; v.currentTime=2; await v.play()}"
                    )
                    page.wait_for_function(
                        "document.querySelector('video').currentTime > 2.1"
                    )
                    page.get_by_role("button", name="Mark start", exact=True).click()
                    assert float(page.locator("#manualStart").input_value()) > 2
                    page.locator("video").evaluate("v => {v.pause(); v.currentTime=5}")
                    page.get_by_role("button", name="Mark end", exact=True).click()
                    assert float(page.locator("#manualEnd").input_value()) == 5
                    page.locator("#manualStart").fill("1")
                    page.locator("#manualEnd").fill("1.4")
                    page.get_by_role("button", name="Preview interval").click()
                    page.wait_for_function(
                        "document.querySelector('video').paused && document.querySelector('video').currentTime >= 1.4"
                    )
                    page.get_by_role("button", name="Save manual segment").click()
                    page.get_by_role("link", name="View in Review").wait_for()
                    assert len([m for m in mutations if m[0].endswith("/manual")]) == 1
                    page.get_by_role("link", name="View in Review").click()
                    page.locator(".card").wait_for()
                    assert page.locator("#kindFilter").input_value() == "manual"
                    assert page.locator("#statusFilter").input_value() == "reviewed"
                    assert page.locator(".score").first.inner_text() == "Manual"
                    assert (
                        page.locator("[data-revealed=true]").get_attribute(
                            "data-slice-id"
                        )
                        == "saved-manual"
                    )
                    assert page.locator(".badge.stale").count() == 0
                    result["scenarios"].extend(
                        [
                            "native-playback-seek",
                            "playhead-marks",
                            "interval-preview",
                            "manual-save-reveal",
                        ]
                    )
                page.goto("http://127.0.0.1:8966/")
                page.locator(".card").first.wait_for()
                assert page.locator("#statusFilter").input_value() == "candidate"
                assert page.locator("#kindFilter").input_value() == ""
                assert_no_overflow(page)
                card = page.locator(".card").first
                cols = page.locator("#list").evaluate(
                    "e => getComputedStyle(e).gridTemplateColumns.split(' ').length"
                )
                assert cols == (2 if width >= 900 else 1)
                content = card.locator(".card-content")
                inner = content.bounding_box()["width"]
                child_cols = content.evaluate(
                    "e => getComputedStyle(e).gridTemplateColumns.split(' ').map(parseFloat)"
                )
                assert len(child_cols) == (2 if inner >= 500 else 1)
                if len(child_cols) == 2:
                    assert abs(child_cols[0] / sum(child_cols) - 0.34) < 0.005
                page.locator("#statusFilter").select_option("")
                page.wait_for_function(
                    "n => document.querySelectorAll('.card').length === n",
                    arg=6 if width == 1366 else 5,
                )
                assert (
                    page.locator(".card").first.get_attribute("data-slice-id")
                    == "slice-0"
                )
                page.locator("#kindFilter").select_option("manual")
                page.wait_for_function(
                    "n => document.querySelectorAll('.card').length === n",
                    arg=2 if width == 1366 else 1,
                )
                assert "1 hidden by filters" in page.locator("#handoffBtn").inner_text()
                page.locator("#sourceFilter").select_option("missing")
                page.get_by_text("No slices to review here.").wait_for()
                page.locator("#profileSelect").focus()
                assert (
                    page.locator("#profileSelect").evaluate(
                        "e => getComputedStyle(e).outlineStyle"
                    )
                    != "none"
                )
                if width in {390, 1366}:
                    page.locator("#sourceFilter").select_option("")
                    page.locator("#kindFilter").select_option("")
                    page.locator(".card").first.wait_for()
                    file = output / f"review-{width}.png"
                    page.screenshot(path=str(file), full_page=True)
                    result["screenshots"].append(str(file))
                assert page.locator("button:visible").evaluate_all(
                    "nodes => nodes.every(e => e.getBoundingClientRect().height >= 36)"
                )
                assert not errors, errors
                result["viewports"].append(
                    {"width": width, "height": height, "geometry": geometry}
                )
                page.close()
            page = browser.new_page(
                viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
            )
            errors, _ = attach(page, "empty")
            page.goto("http://127.0.0.1:8966/sources")
            page.get_by_text("No stored media.").wait_for()
            page.locator("#intake").click()
            assert page.locator("#addOnly").bounding_box()["height"] >= 44
            assert_no_overflow(page)
            assert not errors, errors
            page.close()
            # Intake and reconnect exercise production controls and request wiring.
            for scenario in ["normal", "response-loss"]:
                page = browser.new_page(viewport={"width": 1024, "height": 768})
                errors, mutations = attach(page, scenario)
                page.goto("http://127.0.0.1:8966/sources")
                page.locator("#workspaceProfile option").first.wait_for(
                    state="attached"
                )
                page.locator("#intake summary").first.click()
                page.locator("#sourceRequests").fill("/fixture/source.mp4")
                page.locator("#addOnly").click()
                page.locator("#batchStrip strong").wait_for()
                assert "fixture" in page.locator("#batchStrip").inner_text()
                page.locator("#workspaceProfile").select_option("other")
                assert "fixture" in page.locator("#batchStrip").inner_text()
                page.reload()
                page.locator("#batchStrip strong").wait_for()
                assert len([m for m in mutations if m[0] == "/api/runs"]) == 1
                page.get_by_role("button", name="Stop after current source").click()
                page.get_by_role(
                    "button", name="Stopping after current source"
                ).wait_for()
                assert len([m for m in mutations if m[0].endswith("/stop")]) == 1
                # Render a source-specific failure snapshot through the same production renderer.
                page.evaluate(
                    "workspaceRun = {...workspaceRun, status:'complete', stage:'complete', duplicate_error:'Synthetic duplicate failure', items:[{request:'Long source '.repeat(80),state:'failed',stage:'scoring',error:'Synthetic score failure'}]}; wsRenderRun()"
                )
                page.locator("#batchStrip summary").click()
                assert (
                    "Synthetic score failure"
                    in page.locator("#batchStrip").inner_text()
                )
                assert page.get_by_role("button", name="Retry failed work").is_visible()
                assert_no_overflow(page)
                if scenario == "normal":
                    assert not errors, errors
                else:
                    assert all("ERR_FAILED" in e for e in errors), errors
                page.close()
            page = browser.new_page(viewport={"width": 390, "height": 844})
            errors, _ = attach(page)
            page.goto("http://127.0.0.1:8966/sources")
            page.get_by_role("button", name="Open source").nth(1).click()
            page.get_by_text("Cached media is missing.", exact=False).wait_for()
            page.get_by_role("button", name="Back to sources").click()
            page.get_by_role("button", name="Open source").first.click()
            page.locator("#manualStart").fill("5")
            page.locator("#manualEnd").fill("2")
            page.get_by_role("button", name="Save manual segment").click()
            page.get_by_text("Choose 0 ≤ start < end within the source.").wait_for()
            page.locator("video").dispatch_event("error")
            page.get_by_text("This source cannot play", exact=False).wait_for()
            assert not errors, errors
            page.close()
            page = browser.new_page(viewport={"width": 390, "height": 844})
            _, _ = attach(page, "source-error")
            page.goto("http://127.0.0.1:8966/sources")
            page.get_by_role("button", name="Open source").first.click()
            page.get_by_text("Synthetic source read failure").wait_for()
            page.get_by_role("button", name="Back to sources").click()
            assert page.locator("#sourceLibrary").is_visible()
            page.close()
            result["scenarios"].append("source-read-error-back")
            result["scenarios"].extend(
                [
                    "intake-add-only",
                    "active-run",
                    "frozen-run-profile",
                    "reload-observe-only",
                    "response-loss-observe-only",
                    "stop-wiring",
                    "failed-scoring",
                    "duplicate-error",
                    "missing-media",
                    "unsupported-media",
                    "invalid-manual-interval",
                ]
            )
            result["scenarios"].extend(
                [
                    "review-landing-defaults",
                    "all-kind-order",
                    "combined-filters",
                    "hidden-selected",
                    "narrow-back",
                    "spacing-injection",
                    "keyboard-focus",
                    "coarse-targets",
                    "empty-library",
                ]
            )
        finally:
            browser.close()
    (output / "results.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {
                "viewports": len(result["viewports"]),
                "scenarios": result["scenarios"],
                "output": str(output),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(tempfile.gettempdir()) / "ricesuite-searcher-workspace-browser",
    )
    parser.add_argument("--browser-channel", default=None)
    args = parser.parse_args()
    check(args.output, args.browser_channel)
