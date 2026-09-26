"""The suite never inherits RiceSearcher settings from the developer's shell.

The README documents exporting ``RICESEARCHER_*`` variables. A test that sets
only ``RICESEARCHER_DATA_DIR`` must not follow an inherited
``RICESEARCHER_PROFILES_DIR`` into the developer's real profiles directory.
"""

from __future__ import annotations

import os


def test_no_ricesearcher_setting_leaks_in_from_the_shell() -> None:
    leaked = sorted(k for k in os.environ if k.startswith("RICESEARCHER_"))
    assert leaked == []
