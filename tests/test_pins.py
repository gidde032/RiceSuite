"""One environment, aligned pins (ADR-001 Q6/Q13, fact 1).

The root requirements files are the single authority. Every pillar file that
still declares a dependency (kept so a pillar can run alone as a developer
convenience, ADR-001 Q4) must accept the root pin, so the pillars cannot drift
back into the conflicts that blocked one venv.
"""

import re

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from ricesuite import SUITE_ROOT

ROOT_FILES = ("requirements.txt", "requirements-dev.txt")
PILLAR_FILES = (
    "searcher/requirements.txt",
    "searcher/requirements-dev.txt",
    "clipper/requirements.txt",
    "clipper/requirements-dev.txt",
    "poster/requirements.txt",
)
# Poster's pre-commit hooks build their own venvs from these pins.
PILLAR_HOOK_FILES = ("poster/.pre-commit-config.yaml",)


def _requirements(path: str) -> list[Requirement]:
    reqs = []
    for raw in (SUITE_ROOT / path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        reqs.append(Requirement(line))
    return reqs


def _hook_requirements(path: str) -> list[Requirement]:
    text = (SUITE_ROOT / path).read_text(encoding="utf-8")
    return [
        Requirement(m)
        for m in re.findall(r"^\s*-\s+([A-Za-z0-9_.\-\[\]]+==\S+)", text, re.M)
    ]


def _exact_pins(reqs: list[Requirement]) -> dict[str, str]:
    pins = {}
    for req in reqs:
        specs = list(req.specifier)
        if len(specs) == 1 and specs[0].operator == "==":
            pins[canonicalize_name(req.name)] = specs[0].version
    return pins


ROOT_PINS: dict[str, str] = {}
for _f in ROOT_FILES:
    ROOT_PINS.update(_exact_pins(_requirements(_f)))


def test_root_files_agree_with_each_other():
    runtime = _exact_pins(_requirements("requirements.txt"))
    dev = _exact_pins(_requirements("requirements-dev.txt"))
    clashes = {
        n: (runtime[n], dev[n])
        for n in runtime.keys() & dev.keys()
        if runtime[n] != dev[n]
    }
    assert not clashes


def test_the_shared_web_stack_is_pinned_exactly():
    for name in (
        "fastapi",
        "uvicorn",
        "python-multipart",
        "python-dotenv",
        "anthropic",
        "pydantic",
        "playwright",
    ):
        assert name in ROOT_PINS, f"{name} is not pinned exactly in the root files"


def test_one_anthropic_1x():
    assert ROOT_PINS["anthropic"].split(".")[0] == "1"


def test_playwright_is_unchanged_from_riceposter():
    """Poster's browser fingerprint depends on the Playwright version; the
    consolidation must not move it (fingerprint parity is a burn-in check)."""
    assert ROOT_PINS["playwright"] == "1.62.0"


def test_python_floor_is_3_12():
    text = (SUITE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.12"' in text


@pytest.mark.parametrize("path", PILLAR_FILES + PILLAR_HOOK_FILES)
def test_pillar_declarations_accept_the_root_pins(path):
    reqs = _hook_requirements(path) if path.endswith(".yaml") else _requirements(path)
    assert reqs, f"no requirements parsed from {path}"
    conflicts = {
        str(req): ROOT_PINS[canonicalize_name(req.name)]
        for req in reqs
        if canonicalize_name(req.name) in ROOT_PINS
        and not req.specifier.contains(ROOT_PINS[canonicalize_name(req.name)])
    }
    assert not conflicts, f"{path} rejects the root pin: {conflicts}"


def test_every_pillar_dependency_is_installed_by_the_root_runtime_file():
    runtime = {canonicalize_name(r.name) for r in _requirements("requirements.txt")}
    tooling = {canonicalize_name(r.name) for r in _requirements("requirements-dev.txt")}
    missing = {}
    for path in PILLAR_FILES:
        for req in _requirements(path):
            name = canonicalize_name(req.name)
            if name not in runtime and name not in tooling:
                missing.setdefault(path, []).append(name)
    assert not missing
