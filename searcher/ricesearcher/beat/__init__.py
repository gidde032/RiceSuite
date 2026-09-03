"""The beat profile (D2): the versioned artifact that expresses what makes a
moment clippable for the beat. A natural-language brief + few-shot exemplars feed
the LLM scorer; keywords feed the heuristic prefilter."""

from ricesearcher.beat.profile import BeatProfile, load_profile

__all__ = ["BeatProfile", "load_profile"]
