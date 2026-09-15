"""The beat profile (D2): the versioned artifact that expresses what makes a
moment clippable for the beat. A natural-language brief + few-shot exemplars feed
the LLM scorer; keywords feed the heuristic prefilter."""

from ricesearcher.beat.profile import (
    BeatProfile,
    ensure_seed,
    list_profiles,
    load_profile,
)

__all__ = ["BeatProfile", "ensure_seed", "list_profiles", "load_profile"]
