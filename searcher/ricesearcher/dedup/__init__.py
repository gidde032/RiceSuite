"""Dedup signal (FR-6, D6): an ADVISORY "possible duplicate" annotation.

The library's core promise is *moment-level dedup*, but it is a signal, never a
filter: this layer only sets the ``dup_of``/``dup_score``/``dup_kind`` columns on a
slice. It **never** removes, hides, blocks, deprioritizes, or refuses to store a
slice — the maintainer deliberately reposts similar and re-edited content. Intra-
source duplicates are found by time overlap; cross-source by transcript-embedding
similarity (a local, pluggable embedder). See ``CLAUDE.md`` hard rule #3.
"""

from ricesearcher.dedup.annotate import annotate_duplicates
from ricesearcher.dedup.base import Embedder

__all__ = ["Embedder", "annotate_duplicates"]
