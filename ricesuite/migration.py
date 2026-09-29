"""Explicit, offline copy and cutover of application-owned RiceSuite data.

No pillar modules are imported here: several create directories at import time.
The command holds the launcher lock and refuses known listeners and browser
profiles in use. It never starts a pillar or a browser.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

from ricesuite import SUITE_ROOT, env

POSTER_ENTRIES = (
    "sessions",
    "debug",
    "media",
    "queue_media",
    "queue.jsonl",
    "history.jsonl",
    ".post-in-flight.json",
)
POSTER_BACKUP_PATTERNS = (
    "queue.jsonl.*",
    "history.jsonl.*",
    ".post-in-flight.json.*",
    "debug_*.png",
)
PATH_VARIABLES = (
    *env.DATA_PATHS,
    "RICESEARCHER_PROFILES_DIR",
    "RICECLIPPER_SEARCHER_INBOX",
    "HANDOFF_DIR",
)
MARKER = ".cutover.json"
RECEIPT = ".migration.json"


class MigrationError(RuntimeError):
    """A migration cannot safely proceed without maintainer action."""


def _absolute(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        raise MigrationError(f"expected an absolute path: {path}")
    return path


def _same_or_nested(a: Path, b: Path) -> bool:
    a, b = a.resolve(strict=False), b.resolve(strict=False)
    return a == b or a in b.parents or b in a.parents


def _reject_symlink_ancestors(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise MigrationError(f"path has a symlink ancestor: {part}")


def _manifest(root: Path) -> dict[str, str]:
    """Hash regular files and preserve layout. Reject unsafe links and nodes."""
    result: dict[str, str] = {}
    if root.is_symlink():
        raise MigrationError(f"unsupported symlink: {root}")
    if not root.exists():
        return result
    for path in sorted((root, *root.rglob("*"))):
        relative = str(path.relative_to(root))
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            # Chrome's singleton symlinks point to process/host resources and
            # are deliberately excluded. They cannot be useful when stopped.
            if (
                path.name in {"SingletonLock", "SingletonCookie", "SingletonSocket"}
                and "sessions" in path.parts
            ):
                continue
            raise MigrationError(
                f"unsupported symlink: {path}; remove or resolve it manually"
            )
        if stat.S_ISDIR(mode):
            result[f"{relative}/"] = "directory"
            continue
        if not stat.S_ISREG(mode):
            raise MigrationError(f"unsupported filesystem entry: {path}")
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(chunk)
        result[relative] = h.hexdigest()
    return result


def _sources(config: dict[str, str]) -> dict[str, Path]:
    """The effective pre-cutover data roots, including custom overrides."""
    env.handoff_env(config)
    home = Path.home()
    defaults = {
        "searcher": home / ".ricesearcher",
        "clipper": SUITE_ROOT / "clipper/.riceclipper_work",
        "poster": SUITE_ROOT / "poster",
        "handoff/searcher-to-clipper": home / "ricesearcher-handoff",
        "handoff/clipper-to-poster": home / "riceclipper-handoff",
    }
    result = {}
    for key, (relative, _) in env.DATA_PATHS.items():
        consumer = {
            "RICESEARCHER_HANDOFF_DIR": "RICECLIPPER_SEARCHER_INBOX",
            "RICECLIPPER_HANDOFF_DIR": "HANDOFF_DIR",
        }.get(key)
        raw = config.get(key) or (config.get(consumer) if consumer else None)
        result[relative] = _absolute(Path(raw)) if raw else defaults[relative]
    profile = config.get("RICESEARCHER_PROFILES_DIR")
    if profile:
        profile_path = _absolute(Path(profile))
        if not _same_or_nested(profile_path, result["searcher"]):
            result["searcher-profiles"] = profile_path
        elif profile_path != result["searcher"] / "profiles":
            raise MigrationError(
                "custom Searcher profiles nested unusually inside its library; "
                "relocate them before migration"
            )
    return result


def _entries(sources: dict[str, Path]) -> list[tuple[Path, Path]]:
    result: list[tuple[Path, Path]] = []
    for relative, source in sources.items():
        if relative == "poster":
            candidates = {source / name for name in POSTER_ENTRIES}
            for pattern in POSTER_BACKUP_PATTERNS:
                candidates.update(source.glob(pattern))
            result.extend(
                (path, Path(relative) / path.name)
                for path in sorted(candidates)
                if path.exists() or path.is_symlink()
            )
        elif source.exists():
            result.append((source, Path(relative)))
    return result


def plan(root: Path, config: dict[str, str]) -> dict:
    root = _absolute(root)
    _reject_symlink_ancestors(root)
    if root.exists() or root.is_symlink():
        raise MigrationError(
            f"destination already exists: {root}; choose an empty location"
        )
    sources = _sources(config)
    for source in sources.values():
        _reject_symlink_ancestors(source)
        if _same_or_nested(root, source):
            raise MigrationError(f"destination overlaps source: {root} and {source}")
    for i, (name, source) in enumerate(sources.items()):
        for other_name, other in list(sources.items())[i + 1 :]:
            if _same_or_nested(source, other):
                raise MigrationError(
                    f"source roots overlap: {name}={source}, {other_name}={other}"
                )
    for source in sources.values():
        if source.is_symlink():
            raise MigrationError(f"source root is a symlink: {source}")
    entries = _entries(sources)
    bytes_needed = 0
    for source, _ in entries:
        bytes_needed += (
            sum(
                p.stat().st_size
                for p in (source, *source.rglob("*"))
                if p.is_file() and not p.is_symlink()
            )
            if source.is_dir()
            else source.stat().st_size
        )
        _manifest(source)
    ancestor = root.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    free = shutil.disk_usage(ancestor).free
    if free < bytes_needed + max(bytes_needed // 10, 1024 * 1024):
        raise MigrationError(
            "insufficient space: need at least "
            f"{bytes_needed + max(bytes_needed // 10, 1024 * 1024)} bytes, have {free}"
        )
    return {
        "root": str(root),
        "sources": {k: str(v) for k, v in sources.items()},
        "entries": [(str(a), str(b)) for a, b in entries],
        "bytes": bytes_needed,
        "free": free,
    }


def _copy(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(
            source,
            target,
            symlinks=False,
            ignore=lambda directory, names: [
                n
                for n in names
                if n in {"SingletonLock", "SingletonCookie", "SingletonSocket"}
                and "sessions" in Path(directory).parts
            ],
        )
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _remap(value: str, old: Path, new: Path) -> str:
    path = Path(value)
    if path == old or old in path.parents:
        return str(new / path.relative_to(old))
    return value


def _rewrite_references(root: Path, final_root: Path, sources: dict[str, Path]) -> None:
    db = root / "searcher/library.sqlite3"
    if db.is_file():
        with closing(sqlite3.connect(db)) as conn, conn:
            if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise MigrationError(
                    "copied Searcher SQLite library failed integrity_check"
                )
            for rowid, value in conn.execute("SELECT rowid, media_path FROM sources"):
                if not isinstance(value, str):
                    raise MigrationError("unsupported Searcher media_path value")
                if not Path(value).is_absolute():
                    raise MigrationError(
                        f"unsupported relative Searcher media path: {value}"
                    )
                mapped = _remap(
                    value, sources["searcher"] / "cache", final_root / "searcher/cache"
                )
                if Path(value).is_absolute() and mapped == value:
                    raise MigrationError(
                        f"Searcher media reference is outside the copied cache: {value}"
                    )
                staged_media = (
                    root
                    / "searcher/cache"
                    / Path(mapped).relative_to(final_root / "searcher/cache")
                )
                if not staged_media.is_file():
                    raise MigrationError(f"missing Searcher cache media: {mapped}")
                conn.execute(
                    "UPDATE sources SET media_path=? WHERE rowid=?", (mapped, rowid)
                )
            if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise MigrationError(
                    "rewritten Searcher SQLite library failed integrity_check"
                )
    queue = root / "poster/queue.jsonl"
    if queue.is_file():
        lines = []
        for line in queue.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                lines.append(line)
                continue
            item = json.loads(line)
            if not isinstance(item, dict) or not isinstance(item.get("slots"), list):
                raise MigrationError("unsupported Poster queue record")
            for slot in item["slots"]:
                value = slot.get("media_path")
                if not isinstance(value, str):
                    raise MigrationError("unsupported Poster queue media reference")
                if not Path(value).is_absolute():
                    raise MigrationError(
                        f"unsupported relative Poster queue media: {value}"
                    )
                mapped = _remap(
                    value,
                    sources["poster"] / "queue_media",
                    final_root / "poster/queue_media",
                )
                if Path(value).is_absolute() and mapped == value:
                    raise MigrationError(
                        f"Poster queue media reference outside queue_media: {value}"
                    )
                if (
                    Path(mapped).is_absolute()
                    and mapped != value
                    and not (
                        root
                        / "poster/queue_media"
                        / Path(mapped).relative_to(final_root / "poster/queue_media")
                    ).is_file()
                ):
                    raise MigrationError(f"missing Poster queue media: {mapped}")
                slot["media_path"] = mapped
            lines.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
        queue.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _digest(root: Path) -> str:
    manifest = _manifest(root)
    for name in (MARKER, f"{MARKER}.tmp", RECEIPT, ".pre-migration-env"):
        manifest.pop(name, None)
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def _source_digest(entries: list[tuple[Path, Path]]) -> str:
    snapshots = {str(relative): _manifest(source) for source, relative in entries}
    return hashlib.sha256(json.dumps(snapshots, sort_keys=True).encode()).hexdigest()


def _verify_originals(receipt: dict) -> None:
    sources = {key: Path(value) for key, value in receipt["sources"].items()}
    entries = _entries(sources)
    if [[str(source), str(relative)] for source, relative in entries] != receipt[
        "entries"
    ] or _source_digest(entries) != receipt["source_digest"]:
        raise MigrationError("original data changed after copy; plan and copy again")


def copy(root: Path, config: dict[str, str]) -> dict:
    report = plan(root, config)
    root = Path(report["root"])
    stage = root.with_name(root.name + ".migration-stage")
    if stage.exists():
        raise MigrationError(
            f"partial stage exists: {stage}; inspect and remove it before retrying"
        )
    root.parent.mkdir(parents=True, exist_ok=True)
    stage.mkdir(mode=0o700)
    try:
        sources = {k: Path(v) for k, v in report["sources"].items()}
        originals = {
            str(relative): _manifest(source)
            for source, relative in [(Path(a), Path(b)) for a, b in report["entries"]]
        }
        original_digest = hashlib.sha256(
            json.dumps(originals, sort_keys=True).encode()
        ).hexdigest()
        for source, relative in [(Path(a), Path(b)) for a, b in report["entries"]]:
            _copy(source, stage / relative)
        for source, relative in [(Path(a), Path(b)) for a, b in report["entries"]]:
            if (
                _manifest(source) != originals[str(relative)]
                or _manifest(stage / relative) != originals[str(relative)]
            ):
                raise MigrationError(
                    f"copy verification failed or source changed: {source}"
                )
        _rewrite_references(stage, root, sources)
        if (
            _entries(sources) != [(Path(a), Path(b)) for a, b in report["entries"]]
            or _source_digest([(Path(a), Path(b)) for a, b in report["entries"]])
            != original_digest
        ):
            raise MigrationError("source changed during copy; plan and copy again")
        receipt = {
            "version": 1,
            "sources": report["sources"],
            "entries": report["entries"],
            "source_digest": original_digest,
            "digest": _digest(stage),
        }
        (stage / RECEIPT).write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        os.replace(stage, root)
        return report
    except Exception:
        # Keep the isolated stage for diagnosis. It can never be used by startup.
        raise


def _read_receipt(root: Path) -> dict:
    try:
        receipt = json.loads((root / RECEIPT).read_text(encoding="utf-8"))
        if receipt["version"] != 1 or receipt["digest"] != _digest(root):
            raise MigrationError("copied data changed or failed verification")
        return receipt
    except (OSError, ValueError, KeyError) as exc:
        raise MigrationError(f"missing or invalid migration receipt: {exc}") from exc


def cutover(root: Path, config_file: Path, shell: dict[str, str]) -> None:
    root = _absolute(root)
    receipt = _read_receipt(root)
    _verify_originals(receipt)
    active = [k for k in PATH_VARIABLES if shell.get(k)]
    if active:
        raise MigrationError(
            f"shell path overrides block cutover: {', '.join(active)}; unset them first"
        )
    if config_file.is_symlink():
        raise MigrationError("config file is a symlink")
    current = config_file.read_bytes() if config_file.exists() else None
    marker = root / MARKER
    marker_temp = root / f"{MARKER}.tmp"
    backup = root / ".pre-migration-env"
    state = None
    if marker.exists():
        try:
            state = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MigrationError(f"invalid cutover marker: {exc}") from exc
        if state.get("phase") != "pending":
            raise MigrationError("already cut over")
        old = backup.read_bytes() if state.get("had_config") else None
    else:
        old = current
    remove = set(PATH_VARIABLES) | {"RICESUITE_DATA_DIR"}
    kept = [
        line
        for line in (old or b"").decode("utf-8").splitlines()
        if line.strip().split("=", 1)[0].strip() not in remove
    ]
    additions = [f"RICESUITE_DATA_DIR={root}"]
    if "searcher-profiles" in receipt["sources"]:
        additions.append(f"RICESEARCHER_PROFILES_DIR={root / 'searcher-profiles'}")
    updated = ("\n".join(kept + additions) + "\n").encode("utf-8")
    temporary = config_file.with_name(config_file.name + ".migration-tmp")
    old_digest = hashlib.sha256(old if old is not None else b"").hexdigest()
    new_digest = hashlib.sha256(updated).hexdigest()
    current_digest = hashlib.sha256(current if current is not None else b"").hexdigest()
    if state is not None:
        if (
            state.get("digest") != receipt["digest"]
            or state.get("old_config_digest") != old_digest
            or state.get("config_digest") != new_digest
            or current_digest not in {old_digest, new_digest}
        ):
            raise MigrationError("interrupted cutover state does not match config")
        if current_digest == old_digest:
            if _sources(env.read_env_file(config_file)) != {
                k: Path(v) for k, v in receipt["sources"].items()
            }:
                raise MigrationError("source configuration changed after copy")
            temporary.write_bytes(updated)
            temporary.chmod(0o600)
            os.replace(temporary, config_file)
        state["phase"] = "complete"
        marker_temp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(marker_temp, marker)
        return
    if _sources(env.read_env_file(config_file)) != {
        k: Path(v) for k, v in receipt["sources"].items()
    }:
        raise MigrationError(
            "source configuration changed after copy; plan and copy again"
        )
    backup.write_bytes(old if old is not None else b"")
    backup.chmod(0o600)
    try:
        temporary.write_bytes(updated)
        temporary.chmod(0o600)
        marker_temp.write_text(
            json.dumps(
                {
                    "version": 1,
                    "phase": "pending",
                    "digest": receipt["digest"],
                    "had_config": old is not None,
                    "old_config_digest": old_digest,
                    "config_digest": new_digest,
                }
            ),
            encoding="utf-8",
        )
        os.replace(marker_temp, marker)
        os.replace(temporary, config_file)
        state = json.loads(marker.read_text(encoding="utf-8"))
        state["phase"] = "complete"
        marker_temp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(marker_temp, marker)
    except Exception:
        temporary.unlink(missing_ok=True)
        marker_temp.unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        if old is None:
            config_file.unlink(missing_ok=True)
        else:
            config_file.write_bytes(old)
        raise


def rollback(root: Path, config_file: Path, shell: dict[str, str]) -> None:
    root = _absolute(root)
    marker = root / MARKER
    try:
        state = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MigrationError(f"no valid cutover marker: {exc}") from exc
    if _digest(root) != state["digest"]:
        raise MigrationError(
            "data changed after cutover; rollback would lose or duplicate work. "
            "Reconcile manually"
        )
    _verify_originals(_read_receipt(root))
    if not config_file.is_file() or hashlib.sha256(
        config_file.read_bytes()
    ).hexdigest() != state.get("config_digest"):
        raise MigrationError(
            "configuration changed after cutover; reconcile it manually before rollback"
        )
    if any(shell.get(k) for k in PATH_VARIABLES):
        raise MigrationError("unset shell path overrides before rollback")
    before = (root / ".pre-migration-env").read_bytes()
    if state["had_config"]:
        temporary = config_file.with_name(config_file.name + ".rollback-tmp")
        temporary.write_bytes(before)
        temporary.chmod(0o600)
        os.replace(temporary, config_file)
    else:
        config_file.unlink(missing_ok=True)
    marker.unlink()
