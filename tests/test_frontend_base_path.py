"""Each pillar page works under a gateway prefix (ADR-001 Q11, fact 4).

The gateway serves the pillars at /search/, /clip/ and /post/. Every pillar
page lives at most one path segment deep, so relative URLs resolve under its
own prefix behind the gateway and exactly as before when the pillar runs
alone. A root-absolute URL (`"/api/…"`) would escape to the gateway root and
reach no pillar — or the wrong one.
"""

import re
from pathlib import Path
from urllib.parse import urljoin

import pytest

from ricesuite import SUITE_ROOT

FRONTENDS = {
    "searcher": sorted(
        (SUITE_ROOT / "searcher/ricesearcher/web/static").glob("*.[hj]*")
    ),
    "clipper": [
        SUITE_ROOT / "clipper/web/app.js",
        SUITE_ROOT / "clipper/web/index.html",
    ],
    "poster": [SUITE_ROOT / "poster/frontend/index.html"],
}
# A quoted string starting with one slash and a path segment, or an HTML
# attribute with a root-absolute value. `//` (protocol-relative) and regex
# literals like `/-/g` are not URLs this checks.
ROOT_ABSOLUTE = re.compile(r"""["'`]/(?!/)[A-Za-z][A-Za-z0-9_.-]*[/"'`?]""")
ROOT_PAGE = re.compile(r"""(?:href|src|action)=["']/[^"'/]*["']""")
# Script navigation to a root-absolute target, including the bare site root:
# inside the shell's iframe, "/" is the gateway root, i.e. the shell itself.
ROOT_NAV = re.compile(
    r"""(?:location\.(?:assign|replace)\(|location(?:\.href)?\s*=|window\.open\()"""
    r"""\s*["'`]/(?!/)"""
)


def is_root_absolute(line: str) -> bool:
    """A match counts unless it is a suffix appended with ``+`` (e.g.
    ``"api/slices/" + id + "/window"``), which is not the start of a URL."""
    if ROOT_PAGE.search(line) or ROOT_NAV.search(line):
        return True
    return any(
        not line[: m.start()].rstrip().endswith("+")
        for m in ROOT_ABSOLUTE.finditer(line)
    )


def _code_lines(path: Path) -> list[tuple[int, str]]:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("//", "*", "/*", "<!--")):
            continue
        out.append((n, line))
    return out


@pytest.mark.parametrize("pillar", sorted(FRONTENDS))
def test_frontend_has_no_root_absolute_urls(pillar):
    files = [p for p in FRONTENDS[pillar] if p.suffix in {".js", ".html"}]
    assert files
    offenders = [
        f"{p.relative_to(SUITE_ROOT)}:{n}: {line.strip()}"
        for p in files
        for n, line in _code_lines(p)
        if is_root_absolute(line)
    ]
    assert not offenders, "\n".join(offenders)


def test_detector_catches_each_spelling():
    for sample in (
        'fetch("/api/x")',
        "fetch('/api/x')",
        "fetch(`/api/jobs/${id}`)",
        '<link href="/static/a.css">',
        '<a href="/">Home</a>',
        '<a href="/media">Media</a>',
        'window.location.assign("/")',
        "location.href = '/media'",
        "window.location = `/`",
        'window.open("/api/x")',
    ):
        assert is_root_absolute(sample), sample
    for sample in (
        'fetch("api/x")',
        'href="./"',
        "s.replace(/a/g, '')",
        'src="//cdn/x"',
        'fetch("api/s/" + id + "/window")',
        'window.location.assign("./")',
        'window.open("https://example.org/")',
    ):
        assert not is_root_absolute(sample), sample


@pytest.mark.parametrize(
    ("page", "ref", "standalone", "behind_gateway"),
    [
        ("/", "api/profiles", "/api/profiles", "/search/api/profiles"),
        ("/media", "api/sources", "/api/sources", "/search/api/sources"),
        ("/profiles", "./", "/", "/search/"),
        ("/", "cache/ab/x.mp4", "/cache/ab/x.mp4", "/search/cache/ab/x.mp4"),
    ],
)
def test_relative_urls_resolve_to_the_same_endpoint_both_ways(
    page, ref, standalone, behind_gateway
):
    """One segment deep is what makes relative URLs safe: the page's
    directory is the pillar root in both deployments."""
    assert urljoin("http://h" + page, ref) == "http://h" + standalone
    gateway_page = "/search" + (page if page != "/" else "/")
    assert urljoin("http://h" + gateway_page, ref) == "http://h" + behind_gateway


def test_searcher_serves_its_media_urls_relative():
    source = (SUITE_ROOT / "searcher/ricesearcher/web/app.py").read_text(
        encoding="utf-8"
    )
    assert 'return "cache/" + str(rel)' in source
