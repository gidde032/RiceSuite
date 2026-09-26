"""Stable-documentation regressions (S4 README, S5 handoff dedup)."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_readme_documents_cli_commands() -> None:
    readme = (_ROOT / "README.md").read_text()
    for token in (
        "ricesearcher pull",
        "ricesearcher list",
        "ricesearcher show",
        "ricesearcher profiles",
        "--profile",
    ):
        assert token in readme, f"README must document `{token}`"


def test_readme_documents_runnable_smoke_command() -> None:
    readme = (_ROOT / "README.md").read_text()
    assert "pytest -m smoke --no-cov" in readme


def test_handoff_has_single_reserved_section() -> None:
    handoff = (_ROOT / "handoff.md").read_text()
    assert handoff.count("## Reserved from the agent (maintainer-only)") == 1


def test_readme_offline_network_claims_match_model_loading() -> None:
    """PR #4 review: pull/dedup check the HF Hub on every model load unless
    HF_HUB_OFFLINE is set, so the offline walkthrough must not promise more."""
    readme = (_ROOT / "README.md").read_text()
    assert "it makes no network calls" not in readme
    assert "HF_HUB_OFFLINE=1" in readme


def test_readme_offline_flag_conflict_row_is_order_independent() -> None:
    # argparse names whichever flag came second, so pin only the shared text.
    readme = (_ROOT / "README.md").read_text()
    assert "error: argument --offline: not allowed with argument --model" not in readme
    assert "not allowed with argument" in readme


def test_readme_slices_columns_include_title() -> None:
    readme = (_ROOT / "README.md").read_text()
    assert "rights_risk, source title, window, text" in readme


def test_spec_offline_mode_does_not_claim_credentials_are_unread() -> None:
    # cli.main always loads credentials.env/.env; offline mode just never uses it.
    spec = " ".join((_ROOT / "SPEC.md").read_text().split())  # ignore wrapping
    assert "no credential is read" not in spec
    assert "no credential is required or used" in spec


def test_ui_offline_marker_matches_scorer_constant() -> None:
    """PR #4 review: app.js hardcodes the offline model name; pin it to Python."""
    from ricesearcher.score.heuristic_scorer import OFFLINE_MODEL_NAME

    app_js = (_ROOT / "ricesearcher/web/static/app.js").read_text()
    assert f's.scorer_model === "{OFFLINE_MODEL_NAME}"' in app_js
