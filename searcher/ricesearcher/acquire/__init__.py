"""Acquisition layer (SPEC §8, D1): yt-dlp pull + local watch-folder.

Two front doors, one ``AcquiredSource`` shape feeding the shared pipeline. The
yt-dlp adapter imports its library lazily so the core + tests run without it.
Acquisition NEVER authenticates to or contacts any posting surface.
"""

from ricesearcher.acquire.base import AcquiredSource, Acquirer

__all__ = ["AcquiredSource", "Acquirer"]
