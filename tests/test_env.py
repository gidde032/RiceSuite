"""ricesuite.env: one config file, every existing variable name (ADR-001 Q14)."""

import re
from pathlib import Path

import pytest

from ricesuite import SUITE_ROOT, env

EXAMPLE = SUITE_ROOT / "ricesuite.env.example"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """The default data root is ``~/.ricesuite``. Without this, a machine that
    has completed a real cutover leaks its live marker into these tests."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


# How each pillar spells an environment read. Literal names only; Poster's
# per-slot names are f-strings and are covered by KNOWN_PATTERNS.
_READ_PATTERNS = (
    re.compile(
        r"""(?:getenv|environ\.get|environ\[|setdefault)\(?\s*["']([A-Z][A-Z0-9_]+)["']"""
    ),
    re.compile(r"""_env_(?:int|float)\(\s*["']([A-Z][A-Z0-9_]+)["']"""),
    re.compile(r"""env_bool\(\s*["']([A-Z][A-Z0-9_]+)["']"""),
    re.compile(r"""_ENV\s*=\s*["']([A-Z][A-Z0-9_]+)["']"""),
)
_PILLAR_SOURCES = {
    "searcher": ["ricesearcher"],
    "clipper": ["app", "render", "transcribe"],
    "poster": ["backend"],
}
# Platform/OS variables the pillars consult that are not suite configuration.
_NOT_CONFIG = {"PROGRAMFILES", "LOCALAPPDATA", "HOME", "PATH"}


def _names_read_by_pillars() -> dict[str, str]:
    found: dict[str, str] = {}
    for pillar, packages in _PILLAR_SOURCES.items():
        for package in packages:
            for path in (SUITE_ROOT / pillar / package).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for pattern in _READ_PATTERNS:
                    for name in pattern.findall(text):
                        found.setdefault(name, f"{pillar}/{path.name}")
    return {k: v for k, v in found.items() if k not in _NOT_CONFIG}


def test_scan_finds_the_variables_it_is_meant_to_find():
    """Guard the scanner itself: an empty scan would pass everything below."""
    names = _names_read_by_pillars()
    for expected in (
        "ANTHROPIC_API_KEY",
        "RICESEARCHER_HANDOFF_DIR",
        "RICECLIPPER_WHISPER_MODEL",
        "HANDOFF_DIR",
        "SESSION_CHECK_TTL_S",
        "HEADLESS",
        "RICEPOSTER_DATA_DIR",
    ):
        assert expected in names


def test_every_variable_a_pillar_reads_is_known():
    unknown = {
        n: where for n, where in _names_read_by_pillars().items() if not env.is_known(n)
    }
    assert not unknown, f"pillar reads variables ricesuite.env does not know: {unknown}"


def test_every_known_variable_is_documented_in_the_example():
    text = EXAMPLE.read_text(encoding="utf-8")
    missing = sorted(n for n in env.KNOWN_VARIABLES if n not in text)
    assert not missing, f"ricesuite.env.example never mentions {missing}"
    for probe in (
        "IG_ACCOUNT_A_NAME",
        "IG_ACCOUNT_A_ID",
        "IG_ACCOUNT_A_TOKEN",
        "TT_ACCOUNT_A_TOKEN",
    ):
        assert probe in text and env.is_known(probe)


def test_example_says_the_header_model_also_drives_the_emoji_picker():
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    at = next(i for i, line in enumerate(lines) if "RICECLIPPER_HEADER_MODEL=" in line)
    assert "emoji" in lines[at - 1]


def test_example_parses_to_nothing_but_comments():
    """Copying the example must not silently configure anything."""
    assert env.read_env_file(EXAMPLE) == {}


def test_readme_mock_recipe_sets_every_mutable_data_root():
    readme = (SUITE_ROOT / "README.md").read_text(encoding="utf-8")
    recipe = readme.split("To try it without touching real data", 1)[1].split("```", 2)[
        1
    ]
    assert all(f"{name}=$T/" in recipe for name in env.DATA_PATHS)


def test_missing_file_is_an_empty_config(tmp_path):
    assert env.read_env_file(tmp_path / "absent.env") == {}


def test_values_pass_through_under_their_existing_names(tmp_path):
    f = tmp_path / "ricesuite.env"
    f.write_text(
        "ANTHROPIC_API_KEY=sk-test\n"
        "RICECLIPPER_WHISPER_MODEL=tiny\n"
        "POST_MODE=mock\n"
        "IG_ACCOUNT_B_NAME=Bee\n"
    )
    loaded = env.load(f, base={})
    assert loaded["ANTHROPIC_API_KEY"] == "sk-test"
    assert loaded["RICECLIPPER_WHISPER_MODEL"] == "tiny"
    assert loaded["POST_MODE"] == "mock"
    assert loaded["IG_ACCOUNT_B_NAME"] == "Bee"


def test_shell_wins_over_the_file(tmp_path):
    f = tmp_path / "ricesuite.env"
    f.write_text("POST_MODE=browser\n")
    assert env.load(f, base={"POST_MODE": "mock"})["POST_MODE"] == "mock"


def test_ricesuite_env_variable_selects_the_file(tmp_path):
    f = tmp_path / "other.env"
    f.write_text("LOG_LEVEL=WARNING\n")
    assert env.load(base={"RICESUITE_ENV": str(f)})["LOG_LEVEL"] == "WARNING"


def test_default_file_is_at_the_suite_root():
    assert env.DEFAULT_ENV_FILE == SUITE_ROOT / "ricesuite.env"


def test_unknown_keys_are_reported():
    assert env.unknown_keys(
        {"POST_MODE": "mock", "POST_MOED": "x", "IG_ACCOUNT_Z_TOKEN": "t"}
    ) == ["POST_MOED"]


def test_launcher_owned_keys_are_not_unknown():
    values = {"RICESUITE_GATEWAY_PORT": "9999", "POST_MOED": "x"}
    assert env.unknown_keys(values) == ["POST_MOED"]
    assert env.launcher_keys(values) == ["RICESUITE_GATEWAY_PORT"]


# --- Handoff directories: set by the suite, never mismatched ------------------


def test_default_handoff_dirs_match_the_pillar_defaults():
    got = env.handoff_env({})
    first = str(Path("~/ricesearcher-handoff").expanduser())
    second = str(Path("~/riceclipper-handoff").expanduser())
    assert got == {
        "RICESEARCHER_HANDOFF_DIR": first,
        "RICECLIPPER_SEARCHER_INBOX": first,
        "RICECLIPPER_HANDOFF_DIR": second,
        "HANDOFF_DIR": second,
    }


def test_producer_setting_drives_the_consumer(tmp_path):
    got = env.handoff_env(
        {
            "RICESEARCHER_HANDOFF_DIR": str(tmp_path / "a"),
            "RICECLIPPER_HANDOFF_DIR": str(tmp_path / "b"),
        }
    )
    assert got["RICECLIPPER_SEARCHER_INBOX"] == str(tmp_path / "a")
    assert got["HANDOFF_DIR"] == str(tmp_path / "b")


def test_consumer_only_setting_is_honoured(tmp_path):
    got = env.handoff_env({"HANDOFF_DIR": str(tmp_path / "b")})
    assert got["RICECLIPPER_HANDOFF_DIR"] == str(tmp_path / "b")


def test_matching_ends_are_accepted_after_tilde_expansion():
    home = str(Path("~").expanduser())
    got = env.handoff_env(
        {
            "RICECLIPPER_HANDOFF_DIR": "~/x",
            "HANDOFF_DIR": f"{home}/x",
        }
    )
    assert got["HANDOFF_DIR"] == f"{home}/x"


@pytest.mark.parametrize(
    ("producer", "consumer"),
    [
        ("RICESEARCHER_HANDOFF_DIR", "RICECLIPPER_SEARCHER_INBOX"),
        ("RICECLIPPER_HANDOFF_DIR", "HANDOFF_DIR"),
    ],
)
def test_mismatched_ends_of_a_stage_are_refused(tmp_path, producer, consumer):
    with pytest.raises(env.SuiteConfigError, match=consumer):
        env.handoff_env({producer: str(tmp_path / "p"), consumer: str(tmp_path / "c")})


def test_both_stages_sharing_one_directory_is_refused(tmp_path):
    same = str(tmp_path / "shared")
    with pytest.raises(env.SuiteConfigError, match="different directories"):
        env.handoff_env(
            {"RICESEARCHER_HANDOFF_DIR": same, "RICECLIPPER_HANDOFF_DIR": same}
        )


def test_load_applies_the_derived_handoff_dirs(tmp_path):
    f = tmp_path / "ricesuite.env"
    f.write_text(f"RICESEARCHER_HANDOFF_DIR={tmp_path / 'a'}\n")
    loaded = env.load(f, base={})
    assert loaded["RICECLIPPER_SEARCHER_INBOX"] == str(tmp_path / "a")


def test_load_refuses_a_shell_export_that_contradicts_the_file(tmp_path):
    f = tmp_path / "ricesuite.env"
    f.write_text(f"RICECLIPPER_HANDOFF_DIR={tmp_path / 'b'}\n")
    with pytest.raises(env.SuiteConfigError):
        env.load(f, base={"HANDOFF_DIR": str(tmp_path / "elsewhere")})


def test_fresh_install_uses_unified_paths(tmp_path):
    loaded = env.load(
        tmp_path / "missing.env", base={"RICESUITE_DATA_DIR": str(tmp_path / "suite")}
    )
    assert loaded["RICESEARCHER_DATA_DIR"] == str(tmp_path / "suite/searcher")
    assert loaded["RICECLIPPER_WORK_DIR"] == str(tmp_path / "suite/clipper")
    assert loaded["RICEPOSTER_DATA_DIR"] == str(tmp_path / "suite/poster")
    assert loaded["HANDOFF_DIR"] == str(tmp_path / "suite/handoff/clipper-to-poster")


def test_legacy_install_keeps_old_paths_until_cutover(tmp_path):
    legacy = env.data_env({}, legacy_present=True)
    assert legacy["RICESEARCHER_DATA_DIR"] == str(Path("~/.ricesearcher").expanduser())
    assert legacy["RICEPOSTER_DATA_DIR"] == str(env.SUITE_ROOT / "poster")
    with pytest.raises(env.SuiteConfigError, match="legacy data"):
        env.data_env({"RICESUITE_DATA_DIR": str(tmp_path / "new")}, legacy_present=True)
    root = tmp_path / "new"
    root.mkdir()
    (root / ".cutover.json").write_text('{"version":1,"digest":"fixture"}')
    unified = env.data_env({"RICESUITE_DATA_DIR": str(root)}, legacy_present=True)
    assert unified["RICEPOSTER_DATA_DIR"] == str(root / "poster")


def test_poster_media_alone_marks_an_existing_install(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    media = tmp_path / "checkout/poster/media"
    media.mkdir(parents=True)
    (media / ".gitkeep").touch()
    assert not env._legacy_present(tmp_path / "checkout", under_pytest=False)
    (media / "draft.mp4").write_bytes(b"draft")
    assert env._legacy_present(tmp_path / "checkout", under_pytest=False)


def test_explicit_consumer_override_drives_producer(tmp_path):
    paths = env.data_env({"HANDOFF_DIR": str(tmp_path / "custom")})
    derived = env.handoff_env(paths | {"HANDOFF_DIR": str(tmp_path / "custom")})
    assert derived["RICECLIPPER_HANDOFF_DIR"] == str(tmp_path / "custom")


def test_overlapping_data_roots_are_refused(tmp_path):
    with pytest.raises(env.SuiteConfigError, match="overlap"):
        env.data_env(
            {
                "RICESUITE_DATA_DIR": str(tmp_path),
                "RICESEARCHER_DATA_DIR": str(tmp_path / "poster"),
            }
        )


def test_a_blank_pillar_path_counts_as_unset(tmp_path):
    """#45: a blank shell export must not reach a pillar as a path."""
    loaded = env.load(
        tmp_path / "missing.env",
        base={
            "RICESUITE_DATA_DIR": str(tmp_path / "suite"),
            "RICEPOSTER_DATA_DIR": " ",
        },
    )
    assert loaded["RICEPOSTER_DATA_DIR"] == str(tmp_path / "suite/poster")


def test_prepare_poster_dir_creates_the_unified_poster_dir(tmp_path):
    root = tmp_path / "suite"
    poster = env.prepare_poster_dir(
        {"RICESUITE_DATA_DIR": str(root), "RICEPOSTER_DATA_DIR": str(root / "poster")}
    )
    assert poster == str(root / "poster")
    assert (root / "poster").stat().st_mode & 0o777 == 0o700


def test_prepare_poster_dir_leaves_an_explicit_dir_alone(tmp_path):
    chosen = tmp_path / "chosen"
    poster = env.prepare_poster_dir(
        {
            "RICESUITE_DATA_DIR": str(tmp_path / "suite"),
            "RICEPOSTER_DATA_DIR": str(chosen),
        }
    )
    assert poster == str(chosen)
    assert not chosen.exists()
    assert not (tmp_path / "suite").exists()


def test_prepare_poster_dir_refuses_a_symlinked_root(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(env.SuiteConfigError, match="symlinked"):
        env.prepare_poster_dir(
            {
                "RICESUITE_DATA_DIR": str(link),
                "RICEPOSTER_DATA_DIR": str(link / "poster"),
            }
        )
    assert not (real / "poster").exists()


def test_prepare_poster_dir_reports_an_unusable_path(tmp_path):
    root = tmp_path / "suite"
    root.mkdir()
    (root / "poster").write_text("not a directory")
    with pytest.raises(env.SuiteConfigError, match="cannot create"):
        env.prepare_poster_dir(
            {
                "RICESUITE_DATA_DIR": str(root),
                "RICEPOSTER_DATA_DIR": str(root / "poster"),
            }
        )
