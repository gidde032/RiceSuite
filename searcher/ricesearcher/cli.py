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
from ricesearcher.config import load_config, load_env_files
from ricesearcher.dedup.annotate import SIM_THRESHOLD
from ricesearcher.dedup.embed import SentenceTransformerEmbedder
from ricesearcher.library.cache import MediaCache
from ricesearcher.library.store import Library
from ricesearcher.pipeline import (
    NoAcquirerError,
    annotate_library_duplicates,
    extract_and_score,
    pull,
)
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


def _resolve_source(lib: Library, prefix: str) -> str | None:
    """Resolve a source-id prefix, printing exactly one clean error on failure.

    Distinguishes an ambiguous prefix from a missing one so the caller never
    prints a second, contradictory message.
    """
    try:
        sid = lib.resolve_source_id(prefix)
    except ValueError as exc:  # ambiguous prefix
        print(f"error: {exc}", file=sys.stderr)
        return None
    if sid is None:
        print(f"error: no source {prefix!r}", file=sys.stderr)
    return sid


def _cmd_score(args: argparse.Namespace) -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    profile = load_profile()
    with Library(cfg.db_path) as lib:
        full_id = _resolve_source(lib, args.source_id)
        if full_id is None:
            return 2
        source = lib.get_source(full_id)
        if source is None:  # defensive: a resolved id should always exist
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
            source_id = _resolve_source(lib, args.source)
            if source_id is None:
                return 2
        slices = lib.list_slices(source_id=source_id)
        titles = lib.source_titles()
    if not slices:
        print("no scored slices")
        return 0
    for s in slices:
        span = s.transcript_span[:44].replace("\n", " ")
        title = (titles.get(s.source_id, "") or s.source_id[:8])[:22]
        dup = f"~{s.dup_kind}" if s.dup_of else ""  # advisory duplicate flag
        print(
            f"{s.score:.2f}  {dup:6}  {s.rights_risk:4}  {title:22}  "
            f"{s.target_in:6.0f}-{s.target_out:<6.0f}s  {span!r}"
        )
    return 0


def _slice_label(slice_, titles: dict[str, str]) -> str:
    """Human-identifiable one-liner: source title @ window + transcript snippet."""
    title = (titles.get(slice_.source_id) or f"src {slice_.source_id[:8]}")[:34]
    span = slice_.transcript_span[:56].replace("\n", " ")
    return f"{title!r} @ {slice_.target_in:.0f}-{slice_.target_out:.0f}s  {span!r}"


def _cmd_dedup(args: argparse.Namespace) -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    with Library(cfg.db_path) as lib:
        try:
            annotated = annotate_library_duplicates(
                lib, SentenceTransformerEmbedder(), sim_threshold=args.threshold
            )
        except Exception as exc:  # noqa: BLE001 - CLI boundary: clean message
            print(f"error: dedup failed: {exc}", file=sys.stderr)
            return 2
        titles = lib.source_titles()
    by_id = {s.id: s for s in annotated}
    flagged = sorted((s for s in annotated if s.dup_of), key=lambda s: -s.dup_score)
    print(
        f"dedup (threshold {args.threshold}): {len(flagged)} of {len(annotated)} "
        "slices flagged as possible duplicates (advisory only — nothing removed)"
    )
    for s in flagged:
        canon = by_id.get(s.dup_of)
        print(f"\n  [{s.dup_kind} {s.dup_score:.2f}]")
        print(f"    this:    {_slice_label(s, titles)}")
        canon_label = _slice_label(canon, titles) if canon else f"(missing {s.dup_of})"
        print(f"    ~dup of: {canon_label}")
    return 0


def _cmd_review(args: argparse.Namespace) -> int:  # pragma: no cover - live server
    import uvicorn

    from ricesearcher.web.app import create_app

    print(f"RiceSearcher review UI → http://{args.host}:{args.port}  (Ctrl-C to stop)")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
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

    p_dedup = sub.add_parser(
        "dedup", help="recompute advisory possible-duplicate annotations"
    )
    p_dedup.add_argument(
        "--threshold",
        type=float,
        default=SIM_THRESHOLD,
        help="cross-source cosine similarity threshold (default %(default)s)",
    )
    p_dedup.set_defaults(func=_cmd_dedup)

    p_review = sub.add_parser(
        "review", help="launch the local Slate review UI (select-and-approve gate)"
    )
    p_review.add_argument("--host", default="127.0.0.1")
    p_review.add_argument("--port", type=int, default=8765)
    p_review.set_defaults(func=_cmd_review)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_env_files()  # pick up ANTHROPIC_API_KEY from a local credentials.env/.env
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
