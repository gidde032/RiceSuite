from dataclasses import dataclass


@dataclass
class FakeWord:
    """Duck-typed stand-in for app.models.Word (no pydantic needed)."""

    text: str
    start: float
    end: float


def words(*specs) -> list[FakeWord]:
    """Build words from (text, start, end) tuples."""
    return [FakeWord(t, s, e) for (t, s, e) in specs]
