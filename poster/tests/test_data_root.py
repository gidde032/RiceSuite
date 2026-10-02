"""RICEPOSTER_DATA_DIR: a configurable data root (RiceSuite ADR-001 Q10).

The default must be exactly today's layout — `tests/test_paths.py` pins all
fourteen values against their pre-refactor literals and stays unchanged. This
file covers the new knob: what moves with it, what deliberately does not, and
that it cannot leak into the test suite.

The override is exercised in a subprocess. Under pytest `config` ignores the
variable (the same hermeticity rule as credentials.env), so an in-process
import could only ever observe the default.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend import config
from tests.paths import PROJECT_ROOT

# Everything the app writes follows the data root.
_DATA_PATHS = {
    "config.SESSIONS_ROOT": ("sessions",),
    "config.IG_SESSIONS_DIR": ("sessions", "instagram"),
    "config.TT_SESSIONS_DIR": ("sessions", "tiktok"),
    "config.HEALTH_CACHE_FILE": ("sessions", ".health_cache.json"),
    "config.ACCOUNT_STATE_FILE": ("sessions", ".account-state.json"),
    "config.DEBUG_DIR": ("debug",),
    "config.MEDIA_DIR": ("media",),
    "config.QUEUE_FILE": ("queue.jsonl",),
    "config.QUEUE_MEDIA_DIR": ("queue_media",),
    "config.HISTORY_FILE": ("history.jsonl",),
    "instagram_browser.SESSIONS_DIR": ("sessions", "instagram"),
    "tiktok_browser.SESSIONS_DIR": ("sessions", "tiktok"),
    "session_manager.HEALTH_CACHE_FILE": ("sessions", ".health_cache.json"),
    "queue.QUEUE_FILE": ("queue.jsonl",),
    "queue.QUEUE_MEDIA_DIR": ("queue_media",),
    "scheduler.HISTORY_FILE": ("history.jsonl",),
    "main.HISTORY_FILE": ("history.jsonl",),
}

# Code and tracked assets stay with the checkout.
_CODE_PATHS = {
    "config.ENV_PATH": ("credentials.env",),
    "config.PROMPTS_DIR": ("prompts",),
    "config.FRONTEND_DIR": ("frontend",),
    "main.FRONTEND_DIR": ("frontend",),
}

_PROBE = """
import json
from backend import config, instagram_browser, main, scheduler, session_manager
from backend import queue, tiktok_browser
mods = {"config": config, "instagram_browser": instagram_browser, "main": main,
        "scheduler": scheduler, "session_manager": session_manager,
        "queue": queue, "tiktok_browser": tiktok_browser}
names = json.loads(%r)
print(json.dumps({n: str(getattr(mods[n.split(".")[0]], n.split(".")[1]))
                  for n in names}))
"""


def _suite_environ(tmp_path: Path, lines: tuple[str, ...] = ()) -> dict[str, str]:
    """A child environment that can reach no live suite data (#45).

    With RICEPOSTER_DATA_DIR unset, config asks the suite for the data root.
    HOME and RICESUITE_ENV point into tmp_path, and every inherited suite
    variable is dropped, so the child never reads the live ricesuite.env or
    ~/.ricesuite.
    """
    home = tmp_path / "home"
    home.mkdir()
    env_file = tmp_path / "ricesuite.env"
    env_file.write_text("".join(f"{line}\n" for line in lines))
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("RICE") and k != "HANDOFF_DIR"
    }
    env.update(HOME=str(home), RICESUITE_ENV=str(env_file), POST_MODE="mock")
    return env


def _unified_root(tmp_path: Path) -> Path:
    """A suite data root after cutover, as `rice data cutover` leaves it."""
    root = tmp_path / "suite"
    root.mkdir()
    (root / ".cutover.json").write_text('{"version":1,"digest":"fixture"}')
    return root


def _import_paths_with(
    data_dir: str | None, base: dict[str, str] | None = None
) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env.pop("RICEPOSTER_DATA_DIR", None)
    env["POST_MODE"] = "mock"
    if data_dir is not None:
        env["RICEPOSTER_DATA_DIR"] = data_dir
    names = sorted(_DATA_PATHS) + sorted(_CODE_PATHS)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE % json.dumps(names)],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _join(root: Path, segments: tuple[str, ...]) -> str:
    for segment in segments:
        root = root / segment
    return str(root)


def test_default_data_root_is_the_project_root_itself():
    assert config.DATA_ROOT is config.PROJECT_ROOT


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_unset_or_blank_means_the_project_root(raw):
    assert config.resolve_data_root(raw) is config.PROJECT_ROOT


def test_tilde_expands_without_resolving_symlinks(tmp_path, monkeypatch):
    """`str(path)` reaches Chrome as user_data_dir, so a `.resolve()` would
    turn a symlinked data root into a different profile string."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert str(config.resolve_data_root("~/link")) == str(tmp_path / "link")
    assert str(config.resolve_data_root(f" {link}/ ")) == str(link)


def test_relative_data_root_is_refused_by_name():
    with pytest.raises(ValueError, match="RICEPOSTER_DATA_DIR"):
        config.resolve_data_root("relative/dir")


def test_missing_data_root_is_refused_not_created(tmp_path):
    """A typo'd root must not become a fresh, session-less data set."""
    missing = tmp_path / "typo"
    with pytest.raises(ValueError, match="not an existing directory"):
        config.resolve_data_root(str(missing))
    assert not missing.exists()


@pytest.mark.parametrize("raw", [None, "   "])
def test_unset_variable_follows_the_suite_data_location(tmp_path, raw):
    """#45: outside `rice start`, Poster used to write to the checkout. It now
    uses the Poster directory the launcher would pass, and creates it."""
    root = _unified_root(tmp_path)
    paths = _import_paths_with(
        raw, _suite_environ(tmp_path, (f"RICESUITE_DATA_DIR={root}",))
    )
    for name, segments in _DATA_PATHS.items():
        assert paths[name] == _join(root / "poster", segments), name
    for name, segments in _CODE_PATHS.items():
        assert paths[name] == _join(PROJECT_ROOT, segments), name
    assert (root / "poster").stat().st_mode & 0o777 == 0o700


def test_unset_variable_uses_the_poster_dir_set_in_ricesuite_env(tmp_path):
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    env = _suite_environ(tmp_path, (f"RICEPOSTER_DATA_DIR={chosen}",))
    paths = _import_paths_with(None, env)
    for name, segments in _DATA_PATHS.items():
        assert paths[name] == _join(chosen, segments), name


def test_session_manager_cli_saves_sessions_in_the_suite_data_location(tmp_path):
    """The README login command must route to the suite data location (#45).
    `status` only reads the disk, so no browser opens."""
    root = _unified_root(tmp_path)
    env = _suite_environ(tmp_path, (f"RICESUITE_DATA_DIR={root}",))
    result = subprocess.run(
        [sys.executable, "-m", "backend.session_manager", "status"],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (root / "poster" / "sessions" / "instagram").is_dir()
    assert (root / "poster" / "sessions" / "tiktok").is_dir()


def _import_config(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "from backend import config"],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
    )


def test_a_suite_configuration_error_stops_the_import(tmp_path):
    """An interrupted cutover must stop Poster before it opens any browser."""
    root = _unified_root(tmp_path)
    (root / ".cutover.json").write_text("not json")
    result = _import_config(
        _suite_environ(tmp_path, (f"RICESUITE_DATA_DIR={root}",))
    )
    assert result.returncode != 0
    assert "RiceSuite data location" in result.stderr
    assert not (root / "poster").exists()


def test_a_symlinked_unified_root_is_refused(tmp_path):
    real = _unified_root(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(real)
    result = _import_config(
        _suite_environ(tmp_path, (f"RICESUITE_DATA_DIR={link}",))
    )
    assert result.returncode != 0
    assert "symlinked unified data root" in result.stderr
    assert not (real / "poster").exists()


def test_data_dir_moves_every_written_path_and_nothing_else(tmp_path):
    data = tmp_path / "poster-data"
    data.mkdir()
    paths = _import_paths_with(str(data))
    for name, segments in _DATA_PATHS.items():
        assert paths[name] == _join(data, segments), name
    for name, segments in _CODE_PATHS.items():
        assert paths[name] == _join(PROJECT_ROOT, segments), name
    # Importing config creates media/ and the browser modules create debug/;
    # both must land in the data root, never in the checkout.
    assert (data / "media").is_dir()
    assert (data / "debug").is_dir()


def test_data_dir_is_ignored_under_pytest(tmp_path):
    """Same rule as credentials.env: the suite must not depend on, or reach,
    the maintainer's real data through an exported variable."""
    env = dict(os.environ, RICEPOSTER_DATA_DIR=str(tmp_path / "live"))
    result = subprocess.run(
        [sys.executable, "-c",
         "import pytest; from backend import config; print(config.DATA_ROOT)"],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == str(PROJECT_ROOT)
    assert not (tmp_path / "live").exists()



def test_ignoring_a_set_data_dir_is_reported_at_startup(monkeypatch):
    """If pytest is ever imported into a real server process, the data root
    silently falling back to the checkout would open session-less browser
    profiles against live accounts. The startup check must name it."""
    monkeypatch.setenv("RICEPOSTER_DATA_DIR", "/some/where")
    problems = config.check_startup_config()
    assert any("RICEPOSTER_DATA_DIR" in p and "ignored" in p for p in problems)


def test_unset_data_dir_reports_nothing_about_it(monkeypatch):
    monkeypatch.delenv("RICEPOSTER_DATA_DIR", raising=False)
    assert not any("RICEPOSTER_DATA_DIR" in p for p in config.check_startup_config())
