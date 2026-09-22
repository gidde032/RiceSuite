"""Issue #2: the repo-root ``.env`` is loaded when the app is imported.

The import-time checks run in a subprocess so a fresh interpreter imports
``app.main`` exactly as ``uvicorn app.main:app`` does. The subprocess points
``app.env.DOTENV_PATH`` at a temp file first, so no test ever reads the
developer's real ``.env``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app import env

REPO_ROOT = Path(__file__).resolve().parent.parent

_PROBE = """
import sys
from pathlib import Path
import app.env
app.env.DOTENV_PATH = Path(sys.argv[1])
import app.main
from app import handoff
print(handoff.handoff_root())
"""


def _import_app_with_dotenv(dotenv: Path, **env_overrides: str) -> str:
    child_env = {k: v for k, v in os.environ.items() if k != "RICECLIPPER_HANDOFF_DIR"}
    child_env.update(env_overrides)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, str(dotenv)],
        cwd=REPO_ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return result.stdout.strip().splitlines()[-1]


def test_dotenv_sets_handoff_dir_when_app_is_imported(tmp_path):
    target = tmp_path / "from-dotenv"
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"RICECLIPPER_HANDOFF_DIR={target}\n")

    assert _import_app_with_dotenv(dotenv) == str(target)


def test_dotenv_does_not_override_existing_environment(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"RICECLIPPER_HANDOFF_DIR={tmp_path / 'from-dotenv'}\n")
    exported = tmp_path / "from-shell"

    got = _import_app_with_dotenv(dotenv, RICECLIPPER_HANDOFF_DIR=str(exported))

    assert got == str(exported)


def test_default_dotenv_path_is_the_repo_root():
    assert env.DEFAULT_DOTENV_PATH == REPO_ROOT / ".env"


def test_test_suite_never_reads_the_real_dotenv():
    # tests/conftest.py redirects the path before anything imports app.main.
    assert env.DOTENV_PATH != env.DEFAULT_DOTENV_PATH


def test_load_dotenv_file_missing_file_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.delenv("RICECLIPPER_HANDOFF_DIR", raising=False)

    assert env.load_dotenv_file(tmp_path / "absent.env") is False
    assert "RICECLIPPER_HANDOFF_DIR" not in os.environ


def test_load_dotenv_file_fills_unset_variables(tmp_path, monkeypatch):
    # setenv first so monkeypatch restores the original state on teardown.
    monkeypatch.setenv("RICECLIPPER_WHISPER_MODEL", "")
    monkeypatch.delenv("RICECLIPPER_WHISPER_MODEL")
    monkeypatch.setenv("RICECLIPPER_HANDOFF_DIR", "/from/shell")
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "RICECLIPPER_WHISPER_MODEL=tiny\nRICECLIPPER_HANDOFF_DIR=/from/dotenv\n"
    )

    assert env.load_dotenv_file(dotenv) is True
    assert os.environ["RICECLIPPER_WHISPER_MODEL"] == "tiny"
    assert os.environ["RICECLIPPER_HANDOFF_DIR"] == "/from/shell"
