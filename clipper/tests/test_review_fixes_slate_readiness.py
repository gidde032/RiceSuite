"""Regression contracts from PR #10's final browser-readiness verification."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_narrow_action_bar_does_not_stick_over_review_controls():
    """Browser readiness verification (HIGH): narrow actions cannot cover fields.

    Fix: retain the compact one-column layout but disable sticky positioning at
    the narrow breakpoint, where the three-row bar is too tall to overlay content.
    """
    stylesheet = " ".join((ROOT / "web/style.css").read_text().split())
    narrow_rules = stylesheet.split("@media (max-width: 620px)", maxsplit=1)[1]
    narrow_rules = narrow_rules.split(
        "@media (prefers-reduced-motion: reduce)", maxsplit=1
    )[0]

    assert ".batch-actions { position: static; grid-template-columns: 1fr; }" in (
        narrow_rules
    )
