"""Searcher→Clipper handoff (D8, FR-9, SPEC §7).

On select, RiceSearcher writes a filesystem handoff batch to a shared root,
mirroring the RiceClipper→RicePoster mechanism (one dir per batch; the exact
reviewed interval per selected slice; ``manifest.json`` written LAST as the
atomicity signal; stable ``batch_id``; strict path containment). RiceSearcher
only ever *writes* here — it never deletes, ingests, or contacts RiceClipper.
RiceClipper's delivered "Pull from Searcher" consumer (Issue #8) reads this
contract; see
``docs/integration/riceclipper-pickup-plan.md``.
"""

from ricesearcher.handoff.writer import HandoffEntry, hand_off_selected, write_batch

__all__ = ["HandoffEntry", "hand_off_selected", "write_batch"]
