"""Bounded, process-local observation of existing synchronous operations.

This records progress only: no threads, persistence, retries, or work dispatch.
Each caller supplies an attempt id, so readers never follow another tab's run.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from typing import Any


class Progress:
    def __init__(self, operation_id: str, operation: str, scope: str = "") -> None:
        self._lock = RLock()
        self._data: dict[str, Any] = {
            "operation_id": operation_id,
            "operation": operation,
            "scope": scope,
            "status": "active",
            "stage": "starting",
            "items": [],
            "current": None,
            "completed": 0,
            "total": 0,
            "detail": "",
            "batch_id": "",
            "published": False,
        }

    def notify(self, event: str, **fields: Any) -> None:
        """Accept a coarse stage or item boundary, never elapsed-time guesses."""
        with self._lock:
            if self._data["status"] != "active":
                return
            if event == "items":
                self._data["items"] = [
                    {
                        "id": str(item["id"]),
                        "title": str(item.get("title", "")),
                        "position": item.get("position", i + 1),
                        "state": "waiting",
                        "detail": "",
                    }
                    for i, item in enumerate(fields["items"])
                ]
                self._data["total"] = len(self._data["items"])
                return
            item_id = fields.get("item_id")
            if item_id is not None:
                item = next(
                    (it for it in self._data["items"] if it["id"] == str(item_id)),
                    None,
                )
                if item is not None:
                    item["state"] = event
                    item["detail"] = str(fields.get("detail", ""))
                    self._data["current"] = deepcopy(item)
                self._data["completed"] = sum(
                    it["state"] in {"prepared", "imported", "copied"}
                    for it in self._data["items"]
                )
            else:
                self._data["current"] = None
            self._data["stage"] = event
            self._data["detail"] = str(fields.get("detail", ""))
            if "batch_id" in fields:
                self._data["batch_id"] = str(fields["batch_id"])
            if event == "published":
                self._data["published"] = True

    def finish(
        self, status: str = "complete", detail: str = "", batch_id: str = ""
    ) -> None:
        with self._lock:
            if self._data["status"] != "active":
                return
            if status == "failed" and self._data["published"]:
                status = "unconfirmed"
            self._data["status"] = status
            self._data["detail"] = detail
            self._data["current"] = None
            if batch_id:
                self._data["batch_id"] = batch_id
            if status == "complete":
                self._data["completed"] = self._data["total"]
                for item in self._data["items"]:
                    item["state"] = "complete"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)


class ProgressStore:
    """Retain up to 64 attempts per pillar, preferring active over old results."""

    def __init__(self, limit: int = 64) -> None:
        if limit < 1:
            raise ValueError("progress retention must be positive")
        self._lock = RLock()
        self._limit = limit
        self._runs: OrderedDict[str, Progress] = OrderedDict()

    def start(self, operation_id: str, operation: str, scope: str = "") -> Progress:
        with self._lock:
            existing = self._runs.get(operation_id)
            if existing is not None:
                # Attempt ids are single-use. A second request must not clobber
                # a first request's active or completed observation.
                raise ValueError("progress attempt already exists")
            if len(self._runs) >= self._limit:
                victim = next(
                    (
                        key
                        for key, run in self._runs.items()
                        if run.snapshot()["status"] != "active"
                    ),
                    next(iter(self._runs)),
                )
                del self._runs[victim]
            run = Progress(operation_id, operation, scope)
            self._runs[operation_id] = run
            return run

    def get(self, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(operation_id)
        return run.snapshot() if run is not None else None


def notify(progress: Progress | None, event: str, **fields: Any) -> None:
    """An observer failure must never alter the underlying work's outcome."""
    if progress is not None:
        try:
            progress.notify(event, **fields)
        except Exception:
            pass
