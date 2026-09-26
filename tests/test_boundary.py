"""The posting boundary (ADR-001 Q15): only `poster/` touches posting.

`searcher/` and `clipper/` must never import Playwright or any Poster module.
Checked two ways:

* statically, over the AST of every Python file in both trees (tests, scripts
  and tools included), catching `import x`, `from x import y`, and
  `importlib.import_module("x")` / `__import__("x")` with a literal name;
* dynamically, by importing each pillar's server entry point in a clean
  subprocess and checking what actually ended up in `sys.modules`, which
  catches a transitive import through a helper or third-party module.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ricesuite import SUITE_ROOT

# Poster's top-level packages, plus the automation library only it may use.
FORBIDDEN_ROOTS = frozenset({"playwright", "backend", "poster"})
GUARDED = ("searcher", "clipper")


def _root(module: str) -> str:
    return module.split(".", 1)[0]


def forbidden_imports(source: str) -> list[str]:
    """Module names in ``source`` whose top-level package is forbidden."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        elif isinstance(node, ast.Call):
            func = node.func
            called = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            if called in {"import_module", "__import__"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names = [first.value]
        hits.extend(n for n in names if _root(n) in FORBIDDEN_ROOTS)
    return hits


def _python_files(pillar: str) -> list[Path]:
    return sorted(
        p for p in (SUITE_ROOT / pillar).rglob("*.py") if "__pycache__" not in p.parts
    )


@pytest.mark.parametrize(
    "source",
    [
        "import playwright",
        "from playwright.async_api import async_playwright",
        "import backend.queue",
        "from backend import config",
        "import importlib\nimportlib.import_module('playwright.sync_api')",
        "__import__('backend')",
        "def f():\n    from poster.backend import main",
    ],
)
def test_detector_catches_every_import_spelling(source):
    assert forbidden_imports(source)


@pytest.mark.parametrize(
    "source",
    [
        "import app.main",
        "from ricesearcher import web",
        "x = 'playwright'",  # a mention is not an import
        "from . import backend",  # relative import of a pillar-local module
    ],
)
def test_detector_ignores_legitimate_code(source):
    assert forbidden_imports(source) == []


@pytest.mark.parametrize("pillar", GUARDED)
def test_pillar_never_imports_playwright_or_poster(pillar):
    files = _python_files(pillar)
    assert len(files) > 20, f"scan of {pillar}/ found suspiciously few files"
    offenders = {
        str(p.relative_to(SUITE_ROOT)): hits
        for p in files
        if (hits := forbidden_imports(p.read_text(encoding="utf-8")))
    }
    assert not offenders, f"{pillar}/ imports posting code: {offenders}"


_ENTRY_POINTS = {
    "searcher": ("searcher", "ricesearcher.web.app"),
    "clipper": ("clipper", "app.main"),
}
_PROBE = (
    "import importlib, json, sys; importlib.import_module(sys.argv[1]); "
    "print(json.dumps(sorted({m.split('.')[0] for m in sys.modules})))"
)


@pytest.mark.parametrize("pillar", GUARDED)
def test_entry_point_loads_no_posting_code_at_runtime(pillar, tmp_path):
    cwd, module = _ENTRY_POINTS[pillar]
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "RICESEARCHER_DATA_DIR": str(tmp_path / "searcher-data"),
        "RICESEARCHER_HANDOFF_DIR": str(tmp_path / "h1"),
        "RICECLIPPER_SEARCHER_INBOX": str(tmp_path / "h1"),
        "RICECLIPPER_HANDOFF_DIR": str(tmp_path / "h2"),
    }
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, module],
        cwd=SUITE_ROOT / cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    loaded = set(json.loads(result.stdout.strip().splitlines()[-1]))
    assert _root(module) in loaded
    assert not loaded & FORBIDDEN_ROOTS, loaded & FORBIDDEN_ROOTS
