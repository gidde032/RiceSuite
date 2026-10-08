"""Test config: ensure the project root is importable."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Never read the developer's real .env (Issue #2): app.main loads it at import,
# so redirect the path before any test module imports app.main.
import app.env

app.env.DOTENV_PATH = Path(__file__).resolve().parent / "fixtures" / "no-such.env"


import pytest  # noqa: E402
from ricesuite import env as suite_env  # noqa: E402


@pytest.fixture(autouse=True)
def _hermetic_suite_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset paths resolve through the suite config; keep it in tmp_path.

    A temporary HOME and an absent ricesuite.env stop any test from resolving
    into the developer's real ~/.ricesuite or legacy handoff directories. Every
    data path goes too: the suite derives one stage end from the other (an
    exported Poster HANDOFF_DIR would redirect Clipper's output) and checks
    every path for overlap.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("RICESUITE_ENV", str(tmp_path / "ricesuite.env"))
    for key in (
        "RICESUITE_DATA_DIR",
        *suite_env.DATA_PATHS,
        "RICECLIPPER_SEARCHER_INBOX",
        "HANDOFF_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
