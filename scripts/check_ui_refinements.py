"""Offline browser regressions for the maintainer's PR #43 visual refinements.

Uses production HTML/CSS, synthetic content, and intercepted requests only.
Poster scripts are removed: no backend, account, or posting surface is opened.
"""

# Keep inline JavaScript assertions readable.
# ruff: noqa: E501

import argparse
import json
import re
import tempfile
from pathlib import Path

from check_issue21_browser import ROOT, attach
from playwright.sync_api import sync_playwright

STYLE = """selector => {
 const e=document.querySelector(selector), s=getComputedStyle(e);
 return Object.fromEntries(['fontFamily','fontSize','fontWeight','lineHeight',
 'color','backgroundColor','padding','borderRadius','border','rowGap','columnGap']
 .map(k=>[k,s[k]]));
}"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(tempfile.gettempdir()) / "rice21-refinements",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    html = (ROOT / "poster/frontend/index.html").read_text()
    html = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", html)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        for width, height in (
            (390, 844),
            (490, 350),
            (768, 1024),
            (860, 900),
            (861, 900),
            (1440, 900),
        ):
            context = browser.new_context(viewport={"width": width, "height": height})
            page = context.new_page()

            def route(request):
                if request.request.url.endswith("/slate.css"):
                    request.fulfill(
                        body=(ROOT / "ricesuite/shell/slate.css").read_bytes(),
                        content_type="text/css",
                    )
                elif request.request.url.endswith("/static/logo-ratified.png"):
                    request.fulfill(
                        body=(ROOT / "poster/frontend/logo-ratified.png").read_bytes(),
                        content_type="image/png",
                    )
                else:
                    request.abort()

            page.route("**/*", route)
            page.set_content(
                html.replace(
                    "<head>", '<head><base href="http://rice21.invalid/post/">'
                )
            )
            page.evaluate("""() => {
              document.querySelectorAll('.main > :not(.topbar)').forEach(e=>e.remove());
              document.querySelector('#headerBadges').innerHTML=
                '<span class="badge badge-browser">Browser Mode</span>'+
                '<span class="badge badge-visible badge-toggle" role="button" tabindex="0">Visible Browser</span>';
            }""")
            for content_height in (0, 1600):
                page.evaluate(
                    """height => {
                  document.querySelector('#fixture-content')?.remove();
                  const content=document.createElement('div');content.id='fixture-content';
                  content.style.height=height+'px';document.querySelector('.main').append(content);
                }""",
                    content_height,
                )
                geometry = page.evaluate("""() => {
                  const r=e=>document.querySelector(e).getBoundingClientRect();
                  return {navHeight:r('.sidebar').height,navTop:r('.sidebar').top,
                    headerTop:r('.topbar').top,headerHeight:r('.topbar').height,
                    overflow:document.documentElement.scrollWidth>innerWidth};
                }""")
                if width <= 860:
                    assert geometry["navHeight"] <= 64, (
                        width,
                        content_height,
                        geometry,
                    )
                    assert abs(geometry["headerTop"] - geometry["navHeight"]) < 1, (
                        geometry
                    )
                    assert geometry["headerHeight"] <= 64, geometry
                else:
                    assert (
                        page.evaluate(
                            "getComputedStyle(document.querySelector('.sidebar')).flexDirection"
                        )
                        == "column"
                    )
                assert not geometry["overflow"], (width, geometry)
                results.append(
                    dict(
                        pillar="post",
                        width=width,
                        content_height=content_height,
                        **geometry,
                    )
                )
            page.evaluate(
                "document.querySelector('#fixture-content').style.height='0px'"
            )
            if width in (390, 490, 1440):
                page.screenshot(path=str(args.output / f"post-{width}.png"))
            page.close()
            search, clip = context.new_page(), context.new_page()
            assert not attach(search, "search")
            assert not attach(clip, "clip")
            search.evaluate("""() => {
              observed={id:'fixture',profile:'fixture',unknown:false,snapshot:{
                operation:'handoff',status:'complete',stage:'complete',total:3,completed:3,
                current:null,published:true,batch_id:'fixture-batch',items:[],
                detail:'Batch handed off to Clipper.'}};
              renderOperation();
            }""")
            clip.evaluate(
                "showProgress('Send to Poster','✓ Complete','3 / 3 handed off','Batch handed off to Poster.')"
            )
            for left, right in (
                ("#progress", "#operation-progress"),
                (".progress-top", ".operation-progress-row"),
                (".progress-top strong", ".operation-progress strong"),
                (".progress-count", "#progress-count"),
                (".progress-detail", "#progress-current"),
            ):
                assert search.evaluate(STYLE, left) == clip.evaluate(STYLE, right), (
                    width,
                    left,
                    search.evaluate(STYLE, left),
                    clip.evaluate(STYLE, right),
                )
            for page, selector in (
                (search, "#progress"),
                (clip, "#operation-progress"),
            ):
                page.evaluate(
                    "selector=>document.querySelector(selector).classList.add('error')",
                    selector,
                )
            # Each pillar retains its own semantic error red.
            search_error = search.evaluate(STYLE, ".progress-detail")
            clip_error = clip.evaluate(STYLE, "#progress-current")
            assert (
                search_error.pop("color")
                == search.evaluate(STYLE, "#progress")["color"]
            )
            assert (
                clip_error.pop("color")
                == clip.evaluate(STYLE, "#operation-progress")["color"]
            )
            # Computed zero-width borders inherit that semantic color too.
            search_error.pop("border")
            clip_error.pop("border")
            assert search_error == clip_error, (search_error, clip_error)
            assert clip.evaluate("document.documentElement.scrollWidth<=innerWidth")
            results.append({"pillar": "progress", "width": width, "matched": True})
            if width in (390, 1440):
                for page, selector, name in (
                    (search, "#progress", "search"),
                    (clip, "#operation-progress", "clip"),
                ):
                    page.evaluate(
                        "selector=>document.querySelector(selector).classList.remove('error')",
                        selector,
                    )
                    page.locator(selector).screenshot(
                        path=str(args.output / f"{name}-bar-{width}.png")
                    )
            context.close()
        browser.close()
    (args.output / "checks.json").write_text(json.dumps(results, indent=2) + "\n")
    print(
        f"Passed {len(results)} compact Post / matching progress cases. Evidence: {args.output}"
    )


if __name__ == "__main__":
    main()
