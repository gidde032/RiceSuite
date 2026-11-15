"""Per-call model usage records (RiceSuite #23).

A record holds what the API reported for one request. A token count the
response did not report stays ``None`` ("unavailable"); it is never estimated.
The SDK may retry a request internally (``ricesuite.anthropic_client``); those
transport retries are not visible here, so a record counts one logical request.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


@dataclass
class CallUsage:
    """One request. ``stage`` is ``discovery``, ``refinement`` or ``scoring``."""

    stage: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    elapsed_s: float = 0.0
    stop_reason: str | None = None
    request_id: str | None = None
    error: str = ""
    section: int | None = None
    attempt: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def record_message(record: CallUsage, message: object) -> None:
    """Copy the reported usage, stop reason, and request id onto ``record``."""
    usage = getattr(message, "usage", None)
    for name in TOKEN_FIELDS:
        setattr(record, name, _count(getattr(usage, name, None)))
    stop = getattr(message, "stop_reason", None)
    record.stop_reason = stop if isinstance(stop, str) else None
    request_id = getattr(message, "_request_id", None)
    record.request_id = request_id if isinstance(request_id, str) else None


def summarize(records: list[CallUsage]) -> dict[str, Any]:
    """Totals for one stage. A token total covers only calls that reported it.

    ``<field>_unavailable_calls`` counts the calls without that number, so a
    total is complete only when its unavailable count is 0.
    """
    summary: dict[str, Any] = {
        "calls": len(records),
        "failed_calls": sum(1 for r in records if r.error),
        "retries": sum(1 for r in records if r.attempt > 1),
        "elapsed_s": round(sum(r.elapsed_s for r in records), 3),
        "models": sorted({r.model for r in records}),
    }
    for name in TOKEN_FIELDS:
        values = [getattr(r, name) for r in records]
        summary[name] = sum(v for v in values if v is not None)
        summary[f"{name}_unavailable_calls"] = sum(1 for v in values if v is None)
    return summary
