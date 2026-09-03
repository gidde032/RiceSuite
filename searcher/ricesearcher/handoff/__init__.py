"""Searcher→Clipper handoff (D8, FR-9, SPEC §7).

On select, RiceSearcher writes a filesystem handoff batch to a shared root,
mirroring the RiceClipper→RicePoster mechanism (one dir per batch; a padded-window
clip per selected slice; ``manifest.json`` written LAST as the atomicity signal;
stable ``batch_id``; strict path containment). RiceSearcher only ever *writes*
here — it never deletes, ingests, or contacts RiceClipper. The consumer (a
RiceClipper "Pull from Searcher" pickup) is routed-forward (Issue #8); see
``docs/integration/riceclipper-pickup-plan.md``.
"""

from ricesearcher.handoff.writer import HandoffEntry, hand_off_selected, write_batch

__all__ = ["HandoffEntry", "hand_off_selected", "write_batch"]
