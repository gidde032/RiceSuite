"""Guard tests for the opt-in header contract (SPEC §3, §6.2, SECURITY.md).

The on-screen header generator is the design's only outbound network call. It
must be **opt-in**: nothing (frame or transcript) may leave the machine
automatically after transcription — the user triggers it explicitly. Because the
trigger lives in the browser client, these tests pin the contract against
``web/app.js`` so an accidental re-introduction of an automatic call is caught by
CI rather than shipped.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


def test_no_auto_generate_header_helper():
    """The removed auto-fire helper must stay gone."""
    assert "autoGenerateHeader" not in APP_JS


def test_header_endpoint_is_called_exactly_once():
    """The only fetch to the header endpoint lives in ``requestHeader``."""
    assert APP_JS.count("/header") == 1


def test_request_header_has_a_single_call_site():
    """``requestHeader`` is invoked from exactly one place (the button path).

    Excludes its own ``function requestHeader`` definition; the sole remaining
    call site is inside ``regenerateHeader``, wired to the Generate button.
    """
    call_sites = [
        m.start()
        for m in re.finditer(r"requestHeader\(", APP_JS)
        if not APP_JS[max(0, m.start() - 20) : m.start()].rstrip().endswith("function")
    ]
    assert len(call_sites) == 1


def test_generate_button_triggers_header():
    """The header generator is reachable only via an explicit click handler."""
    assert 'headerGenerateEl.addEventListener("click", () => regenerateHeader(' in (
        APP_JS
    )
