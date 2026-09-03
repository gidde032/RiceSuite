"""RiceSearcher CLI (SPEC §8, D7) — CLI-first before the Slate review UI.

Commands: ``pull`` (acquire+transcribe a source into the library), ``list``
(show sources), ``show`` (print a source's transcript). Never posts or uploads.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from ricesearcher.acquire.watchfolder import WatchFolderAcquirer
from ricesearcher.acquire.ytdlp import YtDlpAcquirer
from ricesearcher.beat.profile import load_profile
from ricesearcher.config import load_config
from ricesearcher.library.cache import MediaCache
from ricesearcher.library.store import Library
from ricesearcher.pipeline import NoAcquirerError, extract_and_score, pull
from ricesearcher.score.anthropic_scorer import AnthropicScorer
from ricesearcher.transcribe.whisper import WhisperTranscriber


def _default_acquirers() -> list:
    # Order matters: local files first (cheap check), then URL.
    return [WatchFolderAcquirer(), YtDlpAcquirer()]


def _cmd_pull(args: argparse.Namespace) -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    cache = MediaCache(cfg.cache_dir)
    with Library(cfg.db_path) as lib:
        try:
            source = pull(
                args.request,
                acquirers=_default_acquirers(),
                transcriber=WhisperTranscriber(model_size=args.model),
                cache=cache,
                library=lib,
            )
        except NoAcquirerError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001 - CLI boundary: any pipeline
            # failure (missing file, transcription/network/OS error) becomes a
            # clean message + exit 2, matching the rest of the CLI's contract
            # instead of dumping a raw traceback.
            print(f"error: pull failed: {exc}", file=sys.stderr)
            return 2
    print(f"pulled {source.id[:12]}  {source.kind.value}  {source.title!r}")
    print(f"  {len(source.words)} transcript words, {source.duration_s:.0f}s")
    return 0


def _cmd_list(_args: argparse.Namespace) -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    with Library(cfg.db_path) as lib:
        sources = lib.list_sources()
    if not sources:
        print("library is empty")
        return 0
    for s in sources:
        print(f"{s.id[:12]}  {s.kind.value:7}  {s.acquired_at:20}  {s.title!r}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    cfg = load_config()
    with Library(cfg.db_path) as lib:
        try:
            full_id = lib.resolve_source_id(args.source_id)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        source = lib.get_source(full_id) if full_id else None
    if source is None:
        print(f"error: no source {args.source_id!r}", file=sys.stderr)
        return 2
    print(f"# {source.title}  ({source.kind.value})")
    print(f"ref: {source.ref}")
    print(source.transcript_text or "(no transcript)")
    return 0


def _resolve_or_error(lib: Library, prefix: str) -> str | None:
    """Resolve a source-id prefix, printing a clean error on ambiguity."""
    try:
        return lib.resolve_source_id(prefix)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def _cmd_score(args: argparse.Namespace) -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    profile = load_profile()
    with Library(cfg.db_path) as lib:
        full_id = _resolve_or_error(lib, args.source_id)
        source = lib.get_source(full_id) if full_id else None
        if source is None:
            print(f"error: no source {args.source_id!r}", file=sys.stderr)
            return 2
        scorer = AnthropicScorer(model=args.model)
        try:
            slices = extract_and_score(
                source, profile=profile, scorer=scorer, library=lib
            )
        except Exception as exc:  # noqa: BLE001 - CLI boundary: clean message
            print(f"error: scoring failed: {exc}", file=sys.stderr)
            return 2
    print(
        f"scored {len(slices)} slices for {source.title!r} "
        f"(beat {profile.version}, model {scorer.model_name})"
    )
    for s in sorted(slices, key=lambda s: -s.score)[:10]:
        span = s.transcript_span[:60].replace("\n", " ")
        print(f"  {s.score:.2f}  {s.target_in:6.0f}-{s.target_out:<6.0f}s  {span!r}")
    return 0


def _cmd_slices(args: argparse.Namespace) -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    with Library(cfg.db_path) as lib:
        source_id = None
        if args.source:
            source_id = _resolve_or_error(lib, args.source)
            if source_id is None:
                print(f"error: no source {args.source!r}", file=sys.stderr)
                return 2
        slices = lib.list_slices(source_id=source_id)
    if not slices:
        print("no scored slices")
        return 0
    for s in slices:
        span = s.transcript_span[:56].replace("\n", " ")
        print(
            f"{s.score:.2f}  {s.rights_risk:4}  {s.source_id[:8]}  "
            f"{s.target_in:6.0f}-{s.target_out:<6.0f}s  {span!r}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ricesearcher")
    sub = parser.add_subparsers(dest="command", required=True)

    p_pull = sub.add_parser("pull", help="acquire + transcribe a source")
    p_pull.add_argument("request", help="a YouTube URL or a local media file path")
    p_pull.add_argument("--model", default="small", help="faster-whisper model size")
    p_pull.set_defaults(func=_cmd_pull)

    p_list = sub.add_parser("list", help="list library sources")
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="print a source's transcript")
    p_show.add_argument("source_id", help="a source id (or its 12-char prefix)")
    p_show.set_defaults(func=_cmd_show)

    p_score = sub.add_parser(
        "score", help="extract + score candidate slices for a source"
    )
    p_score.add_argument("source_id", help="a source id (or prefix) to score")
    p_score.add_argument(
        "--model", default=None, help="Anthropic scorer model (else env/default)"
    )
    p_score.set_defaults(func=_cmd_score)

    p_slices = sub.add_parser("slices", help="list scored candidate slices")
    p_slices.add_argument(
        "--source", default=None, help="filter to one source id (or prefix)"
    )
    p_slices.set_defaults(func=_cmd_slices)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
