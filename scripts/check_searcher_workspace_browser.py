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
from email.parser import BytesParser
from email.policy import default
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
# Required scenario inventory: no missing or duplicate scenario may pass CI.
REQUIRED_SCENARIOS = {
    "native-playback-seek",
    "empty-library",
    "reload-observe-only",
    "frozen-run-profile",
    "active-run",
    "coarse-targets",
    "source-filter-refresh-preserves-choice",
    "invalid-manual-interval",
    "review-landing-defaults",
    "add-and-score-settings",
    "batch-details-focus-preserved",
    "completion-preserves-interval-playhead-focus",
    "unbroken-filename-overflow",
    "interval-preview",
    "source-read-error-back",
    "file-drop-admission",
    "stop-wiring",
    "computed-slate-roles-and-all-source-targets",
    "all-kind-order",
    "clear-files-then-path-intake",
    "combined-filters",
    "missing-media",
    "unsupported-media",
    "keyboard-focus",
    "duplicate-error",
    "playhead-marks",
    "on-demand-duplicates",
    "hidden-selected",
    "selected-source-score",
    "intake-add-only",
    "file-picker-admission",
    "manual-save-reveal",
    "explicit-scoring-retry",
    "spacing-injection",
    "failed-scoring",
    "narrow-back",
    "response-loss-observe-only",
    "review-header-rows",
    "manual-default-end-media-duration",
    "accepted-intake-clears-inputs",
    "manual-save-refreshes-segments",
    "laptop-form-above-fold",
    "workspace-control-heights",
    "segment-mark-grouping",
    "narrow-detail-hides-tools",
    "profiles-inset-alignment",
}

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


def attach(page, scenario="normal", run_state=None, receipts=None):
    errors, mutations = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: (
            errors.append(message.text) if message.type == "error" else None
        ),
    )
    saved = []
    if run_state is None:
        run_state = {"run": None}

    def route(request):
        url = urlsplit(request.request.url)
        path = url.path
        query = parse_qs(url.query)
        if "/api/" in path:
            if request.request.method != "GET":
                mutations.append(
                    (
                        path,
                        (request.request.post_data_buffer or b"").decode(
                            errors="replace"
                        ),
                    )
                )
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
            elif (
                path.endswith(("/runs", "/runs/files"))
                and request.request.method == "POST"
            ):
                filenames = []
                if path.endswith("/files"):
                    message = BytesParser(policy=default).parsebytes(
                        (
                            "Content-Type: "
                            + request.request.headers["content-type"]
                            + "\r\nMIME-Version: 1.0\r\n\r\n"
                        ).encode()
                        + request.request.post_data_buffer
                    )
                    parts = list(message.iter_parts())
                    fields = {
                        part.get_param(
                            "name", header="content-disposition"
                        ): part.get_payload(decode=True).decode()
                        for part in parts
                        if not part.get_filename()
                    }
                    filenames = [
                        part.get_filename() for part in parts if part.get_filename()
                    ]
                    data = {
                        **json.loads(fields["settings_json"]),
                        "attempt_key": fields["attempt_key"],
                    }
                else:
                    data = json.loads(request.request.post_data)
                if receipts is not None:
                    receipts.append({"path": path, "data": data, "files": filenames})
                if scenario == "reject":
                    request.fulfill(
                        status=409,
                        json={
                            "detail": "Scoring credentials are missing; choose offline scoring"
                        },
                    )
                    return
                run_state["run"] = {
                    "id": data["attempt_key"],
                    "profile": data["profile"],
                    "options": data,
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
                if path.endswith("/retry"):
                    data = json.loads(request.request.post_data)
                    if receipts is not None:
                        receipts.append({"path": path, "data": data, "files": []})
                    run_state["run"] = {
                        **run_state["run"],
                        "id": data["attempt_key"],
                        "status": "active",
                        "stage": "scoring",
                    }
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


STYLE_CHECK = "() => {\n const resolve = token => {\n   const e=document.createElement('span'); e.style.color=`var(${token})`;\n   document.body.append(e); const result=getComputedStyle(e).color; e.remove(); return result;\n };\n const checks = [\n   ['body','backgroundColor','--midground'], ['body','color','--text'],\n   ['.topbar','backgroundColor','--backdrop'],\n   ['.media-row','backgroundColor','--interior'], ['.media-row','borderTopColor','--hairline'],\n   ['#sourceRequests','backgroundColor','--interior'], ['#sourceRequests','color','--text'],\n   ['#sourceRequests','borderTopColor','--control-line'],\n   ['#manualStart','backgroundColor','--interior'], ['#manualStart','color','--text'],\n   ['#manualStart','borderTopColor','--control-line'],\n   ['.source-transcript','backgroundColor','--interior'], ['.source-transcript','color','--muted'],\n   ['.source-transcript','borderTopColor','--hairline'],\n   ['.source-player','backgroundColor','--backdrop'], ['.source-inspector .title','color','--rice']\n ];\n const mismatches=[];\n for (const [selector,property,token] of checks) {\n   const value=getComputedStyle(document.querySelector(selector))[property], wanted=resolve(token);\n   if(value!==wanted) mismatches.push({selector,property,token,value,wanted});\n }\n const target=matchMedia('(pointer: coarse)').matches?44:36;\n const elements=[...document.querySelectorAll('button,select,input,textarea,summary,.nav-link')];\n const small=[];\n for (const element of elements) {\n   let e=element.type==='checkbox'?element.closest('label'):element;\n   if(!e || !e.getClientRects().length || !e.checkVisibility()) continue;\n   const r=e.getBoundingClientRect();\n   if(r.height<target-.1 || r.width<target-.1) small.push({tag:e.tagName,id:e.id,text:e.textContent.slice(0,60),width:r.width,height:r.height,target});\n }\n return {mismatches,small,checks:checks.length,target,controls:elements.length};\n}"


def assert_source_styles(page):
    styles = page.evaluate(STYLE_CHECK)
    assert not styles["mismatches"], styles
    assert not styles["small"], styles
    return styles


TARGET_CHECK = "() => { const target=matchMedia('(pointer: coarse)').matches?44:36; return [...document.querySelectorAll('button,select,input,textarea,summary,.nav-link')].map(e => e.type==='checkbox'?e.closest('label'):e).filter(e => e && e.getClientRects().length && e.checkVisibility()).map(e => e.getBoundingClientRect().toJSON()).filter(r => r.height<target-.1 || r.width<target-.1); }"
INTAKE_OPEN = "open => document.querySelectorAll('#intake, #intake details').forEach(d => { d.open = open; })"


def assert_intake_and_detail_styles(page):
    """Check Slate roles and targets with the detail and expanded intake.

    Below 900 px an open detail hides intake, so intake targets are checked
    again from the library view and the source is then reopened.
    """
    page.evaluate(INTAKE_OPEN, True)
    styles = assert_source_styles(page)
    if not page.locator("#sourceRequests").is_visible():
        page.get_by_role("button", name="Back to sources").click()
        assert page.locator("#sourceRequests").is_visible()
        assert page.evaluate(TARGET_CHECK) == [], page.evaluate(TARGET_CHECK)
        page.get_by_role("button", name="Open source").first.click()
        page.locator("#manualStart").wait_for()
    page.evaluate(INTAKE_OPEN, False)
    return styles


def check_review_repairs(browser, result):
    # Contracts/MEDIUM: exercise a real unbroken filename, not just spaced prose.
    title = SOURCES[0]["title"]
    try:
        SOURCES[0]["title"] = "local_filename_without_spaces_" + "x" * 190
        for width in (390, 900):
            page = browser.new_page(viewport={"width": width, "height": 844})
            errors, _ = attach(page)
            page.goto("http://127.0.0.1:8966/sources")
            page.get_by_role("button", name="Open source").first.wait_for()
            assert_no_overflow(page)
            page.get_by_role("button", name="Open source").first.click()
            page.locator("#manualStart").wait_for()
            assert_no_overflow(page)
            assert not errors, errors
            page.close()
    finally:
        SOURCES[0]["title"] = title
    # Contracts + skeptic/MEDIUM: completion must preserve the actual editor node.
    page = browser.new_page()
    errors, _ = attach(page)
    page.goto("http://127.0.0.1:8966/sources")
    page.get_by_role("button", name="Open source").first.click()
    page.locator("#manualStart").fill("2.75")
    page.locator("#manualEnd").fill("7.25")
    page.wait_for_function("document.querySelector('video').readyState >= 2")
    page.evaluate(
        "window.originalInspectionVideo = document.querySelector('video'); originalInspectionVideo.currentTime=6"
    )
    page.locator("#manualStart").focus()
    page.evaluate("wsBatchFinished()")
    assert page.evaluate(
        "document.querySelector('video') === window.originalInspectionVideo"
    )
    assert page.locator("#manualStart").input_value() == "2.75"
    assert page.locator("#manualEnd").input_value() == "7.25"
    assert page.locator("video").evaluate("v => v.currentTime") == 6
    assert page.locator("#manualStart").evaluate("e => e === document.activeElement")
    assert not errors, errors
    page.close()
    # Skeptic/MEDIUM: initial source-filter options may predate the import.
    sources = list(SOURCES)
    SOURCES.clear()
    page = browser.new_page()
    try:
        errors, _ = attach(page)
        page.goto("http://127.0.0.1:8966/")
        page.locator(".card").first.wait_for()
        assert page.locator("#sourceFilter option").count() == 1
        SOURCES.extend(sources)
        page.evaluate("wsBatchFinished()")
        assert page.locator("#sourceFilter option").count() == 3
        page.locator("#sourceFilter").select_option("source")
        page.evaluate("wsBatchFinished()")
        assert page.locator("#sourceFilter").input_value() == "source"
        assert not errors, errors
    finally:
        if not SOURCES:
            SOURCES.extend(sources)
        page.close()
    result["scenarios"].extend(
        [
            "unbroken-filename-overflow",
            "completion-preserves-interval-playhead-focus",
            "source-filter-refresh-preserves-choice",
        ]
    )


def check_primary_actions(browser, result):
    for mode in ("picker", "drop", "add-score", "score-sources", "duplicates"):
        state, receipts = {"run": None}, []
        page = browser.new_page(viewport={"width": 1024, "height": 768})
        page.set_default_timeout(5000)
        errors, _ = attach(page, run_state=state, receipts=receipts)
        page.goto("http://127.0.0.1:8966/sources")
        page.locator("#workspaceProfile option").first.wait_for(state="attached")
        page.locator("#intake > summary").click()
        page.get_by_text("Advanced settings", exact=True).click()
        page.locator("#transcriptionModel").fill("tiny")
        page.locator("#duplicateThreshold").fill("0.72")
        page.locator("#offlineScore").check()
        if mode == "picker":
            page.locator("#sourceFiles").set_input_files(
                ROOT / "tests/fixtures/workspace/source.mp4"
            )
            page.locator("#addOnly").click()
        elif mode == "drop":
            page.evaluate(
                """() => {const transfer = new DataTransfer(); transfer.items.add(new File(['synthetic'], 'dropped.mp4', {type:'video/mp4'})); document.querySelector('#fileDrop').dispatchEvent(new DragEvent('drop', {dataTransfer:transfer,bubbles:true,cancelable:true}));}"""
            )
            page.locator("#addScore").click()
        elif mode == "add-score":
            page.locator("#sourceRequests").fill(
                "https://www.youtube.com/watch?v=fixture\n/fixture/local.mp4"
            )
            page.locator("#addScore").click()
        elif mode == "score-sources":
            page.locator("#sourceLibrary input[type=checkbox]").first.check()
            page.locator("#scoreSources").click()
        else:
            page.locator("#scoringModel").fill("irrelevant-for-duplicates")
            page.locator("#checkDuplicates").click()
        page.locator("#batchStrip strong").wait_for()
        assert len(receipts) == 1, (mode, receipts)
        receipt, data = receipts[0], receipts[0]["data"]
        assert (
            data["profile"] == "fixture"
            and data["transcription_model"] == "tiny"
            and data["threshold"] == 0.72
            and data["offline"]
        )
        assert data["score"] == (mode in {"drop", "add-score", "score-sources"})
        assert data["duplicates_only"] == (mode == "duplicates")
        if mode in {"picker", "drop"}:
            assert receipt["path"] == "/api/runs/files"
            assert receipt["files"] == (
                ["source.mp4"] if mode == "picker" else ["dropped.mp4"]
            )
        if mode == "add-score":
            assert data["requests"] == [
                "https://www.youtube.com/watch?v=fixture",
                "/fixture/local.mp4",
            ]
        if mode == "score-sources":
            assert data["source_ids"] == ["source"]
        # Skeptic/MEDIUM: expanded details and focused summary survive real polling.
        page.locator("#batchStrip summary").click()
        page.wait_for_timeout(950)
        assert page.locator("#batchStrip details").evaluate("e => e.open")
        assert page.locator("#batchStrip summary").evaluate(
            "e => e === document.activeElement"
        )
        if mode == "score-sources":
            state["run"] = {
                **state["run"],
                "status": "complete",
                "stage": "complete",
                "items": [
                    {
                        "source_id": "source",
                        "request": "Synthetic source",
                        "state": "failed",
                        "stage": "scoring",
                        "error": "Synthetic score failure",
                    }
                ],
            }
            page.get_by_role("button", name="Retry failed work").wait_for()
            page.locator("#workspaceProfile").select_option("other")
            page.get_by_role("button", name="Retry failed work").click()
            page.wait_for_function("workspaceRun.status === 'active'")
            assert len(receipts) == 2 and receipts[-1]["path"].endswith("/retry")
            assert set(receipts[-1]["data"]) == {"attempt_key"}
            assert "fixture" in page.locator("#batchStrip strong").inner_text()
        if mode == "picker":
            state["run"]["status"] = "complete"
            page.wait_for_function("workspaceRun.status === 'complete'")
            page.locator("#sourceRequests").fill("/fixture/next.mp4")
            page.locator("#clearSourceFiles").click()
            assert page.locator("#sourceRequests").input_value() == "/fixture/next.mp4"
            assert page.locator("#sourceFiles").evaluate("e => e.files.length") == 0
            page.locator("#addOnly").click()
            page.wait_for_function("workspaceRun.status === 'active'")
            assert len(receipts) == 2 and receipts[-1]["path"] == "/api/runs"
            assert receipts[-1]["data"]["requests"] == ["/fixture/next.mp4"]
        assert not errors, (mode, errors)
        page.close()
    result["scenarios"].extend(
        [
            "file-picker-admission",
            "file-drop-admission",
            "add-and-score-settings",
            "selected-source-score",
            "on-demand-duplicates",
            "explicit-scoring-retry",
            "batch-details-focus-preserved",
            "clear-files-then-path-intake",
        ]
    )


ROW_SPAN = "sel => { const r=[...document.querySelectorAll(sel)].filter(e => e.getClientRects().length).map(e => e.getBoundingClientRect()); return Math.max(...r.map(b => b.bottom)) - Math.min(...r.map(b => b.top)); }"


def _open_source(browser, width, height, scenario="normal", receipts=None):
    page = browser.new_page(viewport={"width": width, "height": height})
    page.set_default_timeout(5000)
    errors, mutations = attach(page, scenario, receipts=receipts)
    page.goto("http://127.0.0.1:8966/sources")
    page.get_by_role("button", name="Open source").first.click()
    page.locator("#manualStart").wait_for()
    return page, errors, mutations


def _audit_review_header(browser):
    # Wide Verdana stands in for wider platform fonts, such as CI's Linux fonts.
    for (width, height), font in [
        (size, font) for size in [(1366, 768), (1920, 1080)] for font in ("", "Verdana")
    ]:
        page = browser.new_page(viewport={"width": width, "height": height})
        attach(page)
        page.goto("http://127.0.0.1:8966/")
        page.locator(".card").first.wait_for()
        if font:
            page.add_style_tag(
                content=f"body, button, select {{ font-family: {font} !important; }}"
            )
        # Kind/Source filters must not push Send selected onto its own row.
        span = page.evaluate(ROW_SPAN, ".filters > *")
        assert span < 50, (width, font, "filters wrap", span)
        assert page.locator("#handoffBtn").bounding_box()["x"] > width / 2
        page.close()
    for path in ("", "sources", "profiles.html"):
        page = browser.new_page(viewport={"width": 390, "height": 844})
        attach(page)
        page.goto("http://127.0.0.1:8966/" + path)
        position = page.locator(".topbar").evaluate("e => getComputedStyle(e).position")
        assert position == "static", (path, position)
        page.close()


def _audit_default_end(browser):
    # URL metadata durations are rounded; the player owns the real media length.
    duration = SOURCES[0]["duration_s"]
    try:
        SOURCES[0]["duration_s"] = 12.4
        page, errors, mutations = _open_source(browser, 1024, 768)
        page.wait_for_function("document.querySelector('video').readyState >= 1")
        page.locator("#manualStart").fill("10")
        page.get_by_role("button", name="Save manual segment").click()
        page.get_by_role("link", name="View in Review").wait_for(timeout=2000)
        body = json.loads([m for m in mutations if m[0].endswith("/manual")][0][1])
        assert body["target_out"] == 12, body
        assert not errors, errors
        page.close()
    finally:
        SOURCES[0]["duration_s"] = duration


def _audit_intake_clears(browser):
    for scenario in ("normal", "reject"):
        state, receipts = {"run": None}, []
        page = browser.new_page(viewport={"width": 1024, "height": 768})
        page.set_default_timeout(5000)
        attach(page, scenario, run_state=state, receipts=receipts)
        page.goto("http://127.0.0.1:8966/sources")
        page.locator("#workspaceProfile option").first.wait_for(state="attached")
        page.locator("#intake > summary").click()
        page.locator("#sourceFiles").set_input_files(
            ROOT / "tests/fixtures/workspace/source.mp4"
        )
        page.locator("#addOnly").click()
        page.locator("#batchStrip p, #batchStrip strong").first.wait_for()
        page.wait_for_function("!workspaceBusy")
        kept = page.locator("#sourceFiles").evaluate("e => e.files.length")
        if scenario == "reject":
            # A refused batch keeps its input so the user can correct and resend.
            assert kept == 1 and page.evaluate("wsFiles.length") == 1
            page.close()
            continue
        assert kept == 0 and page.evaluate("wsFiles.length") == 0, "files kept"
        state["run"]["status"] = "complete"
        page.wait_for_function("workspaceRun.status === 'complete'")
        page.locator("#sourceRequests").fill("/fixture/next.mp4")
        page.locator("#addOnly").click()
        page.wait_for_function("workspaceRun.status === 'active'")
        assert receipts[-1]["path"] == "/api/runs", receipts[-1]
        assert page.locator("#sourceRequests").input_value() == "", "requests kept"
        page.close()


def _audit_segments_refresh(browser):
    page, errors, _ = _open_source(browser, 1024, 768)
    summary = page.locator(".source-segments")
    before = summary.inner_text().count("Manual / unscored")
    page.locator("#manualStart").fill("1")
    page.locator("#manualEnd").fill("2")
    page.get_by_role("button", name="Save manual segment").click()
    page.get_by_role("link", name="View in Review").wait_for()
    page.wait_for_function(
        "n => document.querySelector('.source-segments').innerText.split('Manual / unscored').length - 1 === n",
        arg=before + 1,
        timeout=2000,
    )
    assert not errors, errors
    page.close()


def _audit_laptop_fold(browser):
    page, errors, _ = _open_source(browser, 1366, 768)
    bottom = page.locator(".segment-form").bounding_box()
    assert bottom["y"] + bottom["height"] <= 768, bottom
    page.locator("#intake > summary").click()
    assert page.locator("#sourceRequests").is_visible()
    assert_no_overflow(page)
    page.close()


def _audit_control_heights(browser):
    for width in (390, 1366):
        page, _, _ = _open_source(browser, width, 844)
        heights = page.evaluate(
            "[...document.querySelectorAll('.segment-form input, .segment-form button')].map(e => e.getBoundingClientRect().height)"
        )
        assert max(heights) - min(heights) < 0.5, (width, heights)
        if width < 900:
            page.get_by_role("button", name="Back to sources").click()
        page.locator("#intake > summary").click()
        page.get_by_text("Advanced settings", exact=True).click()
        pair = [
            page.locator(s).bounding_box()["height"]
            for s in ("#duplicateThreshold", "#transcriptionModel")
        ]
        assert abs(pair[0] - pair[1]) < 0.5, (width, pair)
        page.close()


def _audit_mark_grouping(browser):
    page, _, _ = _open_source(browser, 390, 844)
    for field, mark in (("#manualStart", "Mark start"), ("#manualEnd", "Mark end")):
        button = page.get_by_role("button", name=mark, exact=True)
        assert page.locator(field).evaluate(
            "(e, b) => e.closest('.segment-field')?.contains(b) || false",
            button.element_handle(),
        ), mark
        a, b = page.locator(field).bounding_box(), button.bounding_box()
        assert abs((a["y"] + a["height"]) - (b["y"] + b["height"])) < 1, (mark, a, b)
    page.close()


def _audit_narrow_tools(browser):
    for width in (390, 900):
        page, _, _ = _open_source(browser, width, 844)
        tools = page.locator(".workspace-tools")
        assert tools.is_visible() == (width >= 900), (width, "tools")
        if width < 900:
            page.get_by_role("button", name="Back to sources").click()
            assert tools.is_visible()
        page.close()


def _audit_profiles_inset(browser):
    for width in (390, 1024):
        page = browser.new_page(viewport={"width": width, "height": 800})
        attach(page)
        page.goto("http://127.0.0.1:8966/profiles.html")
        insets = page.evaluate(
            "['.topbar', '.profile-list'].map(s => parseFloat(getComputedStyle(document.querySelector(s)).paddingLeft))"
        )
        assert insets[0] == insets[1] == (16 if width >= 900 else 8), (width, insets)
        page.close()


AUDIT_REPAIRS = {
    "review-header-rows": _audit_review_header,
    "manual-default-end-media-duration": _audit_default_end,
    "accepted-intake-clears-inputs": _audit_intake_clears,
    "manual-save-refreshes-segments": _audit_segments_refresh,
    "laptop-form-above-fold": _audit_laptop_fold,
    "workspace-control-heights": _audit_control_heights,
    "segment-mark-grouping": _audit_mark_grouping,
    "narrow-detail-hides-tools": _audit_narrow_tools,
    "profiles-inset-alignment": _audit_profiles_inset,
}


def check_audit_repairs(browser, result):
    # PR #97 audit repairs. Each scenario reports independently.
    failures = {}
    for name, audit in AUDIT_REPAIRS.items():
        try:
            audit(browser)
        except Exception as exc:  # noqa: BLE001 - report every failed repair
            failures[name] = f"{type(exc).__name__}: {exc}"[:300]
    assert not failures, json.dumps(failures, indent=2)
    result["scenarios"].extend(AUDIT_REPAIRS)


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
                    result.setdefault("styles", []).append(
                        assert_intake_and_detail_styles(page)
                    )
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
            check_review_repairs(browser, result)
            check_primary_actions(browser, result)
            page = browser.new_page(
                viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
            )
            errors, _ = attach(page)
            page.goto("http://127.0.0.1:8966/sources")
            page.get_by_role("button", name="Open source").first.click()
            page.locator("#manualStart").wait_for()
            result.setdefault("styles", []).append(
                assert_intake_and_detail_styles(page)
            )
            assert_no_overflow(page)
            assert not errors, errors
            page.close()
            result["scenarios"].append("computed-slate-roles-and-all-source-targets")
            check_audit_repairs(browser, result)
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
    assert set(result["scenarios"]) == REQUIRED_SCENARIOS, result["scenarios"]
    assert len(result["scenarios"]) == len(REQUIRED_SCENARIOS)
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
