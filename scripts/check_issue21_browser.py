"""Fixture-only Issue #21 browser checks; no backend or live data is accessed.

Requires the existing developer Playwright installation and Chrome. All network
requests are intercepted. Results/screenshots go to a temporary directory.
"""

# Inline browser assertions keep JavaScript expressions together.
# ruff: noqa: E501

import argparse
import json
import mimetypes
import re
import runpy
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "bfa2d51"
VIEWPORTS = [
    (390, 844),
    (768, 1024),
    (899, 800),
    (900, 800),
    (1024, 768),
    (1366, 768),
    (1440, 900),
    (1920, 1080),
]
EDITOR_CHECKS = runpy.run_path(str(ROOT / "clipper/scripts/check_editor_browser.py"))[
    "CHECKS"
]
SLICES = [
    {
        "id": f"slice-{i}",
        "status": "selected",
        "profile_id": "fixture",
        "score": 1 - i / 10,
        "source_title": f"Clip {i} — " + "LongName" * 18,
        "target_in": 2,
        "target_out": 20,
        "media_url": "/fixture.mp4",
        "rights_risk": "medium",
        "heuristic_score": 0.8,
        "scorer_model": "heuristic-offline",
        "rationale": "Synthetic review explanation.",
        "transcript_span": "Synthetic transcript for human review.",
        "beat_profile_version": 1,
        "stale": True,
    }
    for i in range(1, 6)
]
SNAPSHOT = {
    "operation_id": "fixture-operation",
    "operation": "handoff",
    "scope": "fixture",
    "status": "active",
    "stage": "preparing",
    "total": 3,
    "completed": 1,
    "current": {"id": "slice-2", "position": 2, "title": "Long clip title " * 30},
    "detail": "",
    "batch_id": "",
    "published": False,
    "items": [
        {
            "id": f"slice-{i}",
            "position": i,
            "title": f"Clip {i}",
            "state": "prepared" if i == 1 else "preparing" if i == 2 else "waiting",
        }
        for i in range(1, 4)
    ],
}
GEOMETRY = """() => {
 const card=window.__editorFixture.el, origin=card.getBoundingClientRect();
 const selectors=['.review-grid','.preview-col','.edit-col','.clip-result','.result-frame','.output-video','.download-link','.transcript-panel','.lyrics','.transcript','.lyrics-input','.header-input','.clip-status'];
 return selectors.map(selector=>{const e=card.querySelector(selector); if(!e)return null; const r=e.getBoundingClientRect(),s=getComputedStyle(e);
 return {selector,x:r.width?Math.round((r.x-origin.x)*100)/100:0,y:r.height?Math.round((r.y-origin.y)*100)/100:0,width:Math.round(r.width*100)/100,height:Math.round(r.height*100)/100,
 style:['display','fontFamily','fontSize','lineHeight','padding','border','backgroundColor','color','objectFit'].map(k=>s[k])};});
}"""
SEARCH_CHECKS = """() => {
 const issues=[], check=(v,m)=>{if(!v)issues.push(m)};
 const cards=[...document.querySelectorAll('.card')],rect=e=>e.getBoundingClientRect();
 check(cards.map(c=>c.dataset.sliceId).join(',')==='slice-1,slice-2,slice-3,slice-4,slice-5','score/DOM order');
 const columns=getComputedStyle(document.querySelector('.list')).gridTemplateColumns.split(' ').length;
 check(columns===(innerWidth>=900?2:1),'column breakpoint');
 if(columns===2){check(Math.abs(rect(cards[0]).y-rect(cards[1]).y)<1,'first row');check(rect(cards[2]).y>rect(cards[0]).y,'row-major order');check(rect(cards[4]).x===rect(cards[0]).x,'odd last card');}
 for(const card of cards){const content=card.querySelector('.card-content'),p=card.querySelector('.preview'),m=card.querySelector('.meta');
 const stacked=rect(m).y>=rect(p).bottom;
 check(stacked===(rect(content).width<500),'card content breakpoint');
 check(card.querySelectorAll('.actions button').length===3 && card.querySelectorAll('.window-edit input').length===2,'complete controls');
 check(getComputedStyle(card.querySelector('video')).objectFit==='contain','full source frame');}
 check(document.documentElement.scrollWidth<=innerWidth,'horizontal overflow');
 check(document.querySelector('#progress').innerText.includes('1 / 3 prepared'),'actual progress count');
 check(document.querySelector('#progress').getAttribute('role')==='status','status semantics');
 return {issues,columns,width:innerWidth};
}"""


def asset(path, baseline):
    if baseline and path.startswith("clipper/web/"):
        return subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT)
    return (ROOT / path).read_bytes()


def attach(page, pillar, baseline=False):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def route(request):
        path = urlsplit(request.request.url).path
        if path.endswith("/api/profiles"):
            request.fulfill(json=[{"id": "fixture", "name": "Fixture", "selected": 5}])
        elif path.endswith("/api/slices"):
            request.fulfill(json=SLICES)
        elif path.endswith("/api/health"):
            request.fulfill(json={"ffmpeg": True, "libass": True})
        elif path.endswith("/api/media-info"):
            request.fulfill(json={"job_dirs": 0, "files": 0, "total_bytes": 0})
        elif "/api/" in path:
            request.fulfill(status=404, json={"detail": "fixture only"})
        else:
            name = path.rsplit("/", 1)[-1]
            if name in ("", "index.html"):
                file = (
                    "clipper/web/index.html"
                    if pillar == "clip"
                    else "searcher/ricesearcher/web/static/index.html"
                )
            elif name == "slate.css":
                file = "ricesuite/shell/slate.css"
            elif name in (
                ("app.js", "style.css", "slate-logo.png")
                if pillar == "clip"
                else ("app.js", "styles.css", "mark.png")
            ):
                file = (
                    "clipper/web/"
                    if pillar == "clip"
                    else "searcher/ricesearcher/web/static/"
                ) + name
            else:
                request.fulfill(status=404, body="fixture only")
                return
            request.fulfill(
                body=asset(file, baseline),
                content_type=mimetypes.guess_type(file)[0]
                or "application/octet-stream",
            )

    page.route("**/*", route)
    page.goto(f"http://rice21.invalid/{pillar}/", wait_until="networkidle")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=Path(tempfile.gettempdir()) / "rice21-browser"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    old_html = asset("clipper/web/index.html", True).decode()
    new_html = asset("clipper/web/index.html", False).decode()
    template = r'<template id="clip-card-template">[\s\S]*?</template>'
    assert (
        re.search(template, old_html).group() == re.search(template, new_html).group()
    ), "Clipper slot template changed"
    assert asset("clipper/web/style.css", False).startswith(
        asset("clipper/web/style.css", True)
    ), "Clipper existing CSS changed"
    results, failures = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        for width, height in VIEWPORTS:
            context = browser.new_context(viewport={"width": width, "height": height})
            page = context.new_page()
            errors = attach(page, "search")
            page.wait_for_selector(".card")
            page.evaluate(
                "snapshot => {observed={id:snapshot.operation_id,profile:'fixture',unknown:false,snapshot};renderOperation();}",
                SNAPSHOT,
            )
            result = page.evaluate(SEARCH_CHECKS)
            for state in ("failed", "unconfirmed", "complete"):
                page.evaluate(
                    "state => {observed.snapshot.status=state;observed.snapshot.detail='Long actionable message '+ 'filename'.repeat(80);renderOperation();}",
                    state,
                )
                assert page.evaluate(
                    "document.documentElement.scrollWidth<=innerWidth"
                ), (width, state)
            if width in (390, 1440):
                page.evaluate(
                    "snapshot => {observed.snapshot=snapshot;renderOperation();}",
                    SNAPSHOT,
                )
                page.screenshot(
                    path=str(args.output / f"implemented-search-{width}.png"),
                    full_page=True,
                )
            for text in (
                "Loading slices…",
                "No slices to review here.",
                "Failed to load slices: fixture",
            ):
                page.evaluate(
                    "text=>listEl.replaceChildren(el('div',{class:'empty'},text))", text
                )
                assert page.evaluate(
                    "getComputedStyle(listEl.firstElementChild).gridColumn==='1 / -1'"
                )
            result["errors"] = errors
            results.append(dict(pillar="search", **result))
            failures.extend(result["issues"] + errors)
            page.close()
            baseline = context.new_page()
            current = context.new_page()
            old_errors = attach(baseline, "clip", True)
            new_errors = attach(current, "clip")
            for mode in ("speech", "music"):
                current.evaluate(
                    "showProgress('Clip progress','○ Idle','','Choose clips or pull a batch to begin.')"
                )
                for target in (baseline, current):
                    target.evaluate(
                        "if(window.__editorFixture)window.__editorFixture.resultEl.className='clip-result is-empty'"
                    )
                old = baseline.evaluate(EDITOR_CHECKS.replace("MODE", json.dumps(mode)))
                new = current.evaluate(EDITOR_CHECKS.replace("MODE", json.dumps(mode)))
                old_geometry, new_geometry = (
                    baseline.evaluate(GEOMETRY),
                    current.evaluate(GEOMETRY),
                )
                assert old_geometry == new_geometry, (
                    width,
                    mode,
                    "editor geometry/style changed",
                    [
                        (a, b)
                        for a, b in zip(old_geometry, new_geometry, strict=True)
                        if a != b
                    ],
                )
                result = {
                    "pillar": "clip",
                    "width": width,
                    "mode": mode,
                    "issues": new["issues"],
                    "baseline_issues": old["issues"],
                }
                results.append(result)
                failures.extend(new["issues"])
                for state in ("empty", "busy", "stale"):
                    for target in (baseline, current):
                        target.evaluate(
                            "state=>{const r=window.__editorFixture.resultEl;r.classList.remove('is-empty','is-busy','is-stale');r.classList.add('is-'+state);}",
                            state,
                        )
                    assert baseline.evaluate(GEOMETRY) == current.evaluate(GEOMETRY), (
                        width,
                        mode,
                        state,
                    )
                current.evaluate(
                    "snapshot=>snapshotProgress({...snapshot,operation:'send',stage:'copying',current:{title:'LongName'.repeat(80),position:2},detail:'Error '.repeat(100)})",
                    SNAPSHOT,
                )
                assert current.evaluate(
                    "document.documentElement.scrollWidth<=innerWidth"
                ), (width, mode, "bar overflow")
                if width in (390, 1440) and mode == "speech":
                    current.evaluate(
                        "snapshot=>snapshotProgress({...snapshot,operation:'send',stage:'copying',current:{title:'Review fixture',position:2},detail:''})",
                        SNAPSHOT,
                    )
                    current.screenshot(
                        path=str(args.output / f"implemented-clip-{width}.png"),
                        full_page=True,
                    )
            failures.extend(old_errors + new_errors)
            context.close()
        browser.close()
    (args.output / "browser-checks.json").write_text(
        json.dumps({"base": BASE, "results": results, "failures": failures}, indent=2)
        + "\n"
    )
    if failures:
        raise AssertionError("\n".join(failures))
    print(
        f"Passed {len(results)} production browser cases; unchanged Clipper template/CSS and relative geometry at all eight viewports. Evidence: {args.output}"
    )


if __name__ == "__main__":
    main()
