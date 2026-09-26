"""Installed artifacts contain every runtime asset used by stable entry points."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def test_wheel_contains_profile_and_web_assets_and_can_seed(
    tmp_path: Path, monkeypatch
) -> None:
    # A user's shell may export any documented RICESEARCHER_* setting. The cold
    # install must ignore them all, never seeding into a real profiles dir.
    shell_profiles = tmp_path / "users-real-profiles"
    monkeypatch.setenv("RICESEARCHER_PROFILES_DIR", str(shell_profiles))
    monkeypatch.setenv("RICESEARCHER_HANDOFF_DIR", str(tmp_path / "users-handoff"))
    source_root = tmp_path / "source"
    source_root.mkdir()
    shutil.copy2(PROJECT_ROOT / "pyproject.toml", source_root / "pyproject.toml")
    shutil.copytree(PROJECT_ROOT / "ricesearcher", source_root / "ricesearcher")
    wheel_dir = tmp_path / "wheelhouse"
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=source_root,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheel = next(wheel_dir.glob("ricesearcher-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "ricesearcher/beat/profiles/default.json" in names
        assert "ricesearcher/web/static/app.js" in names
        assert "ricesearcher/web/static/profiles.html" in names
        archive.extractall(tmp_path / "installed")

    data_dir = tmp_path / "cold-data"
    env = {k: v for k, v in os.environ.items() if not k.startswith("RICESEARCHER_")}
    env["PYTHONPATH"] = str(tmp_path / "installed")
    env["RICESEARCHER_DATA_DIR"] = str(data_dir)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from ricesearcher.beat.profile import ensure_seed; ensure_seed()",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert (data_dir / "profiles" / "example-beat.json").is_file()
    assert not shell_profiles.exists()
