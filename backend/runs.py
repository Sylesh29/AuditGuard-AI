"""Per-run state. Each upload gets its own Run, so concurrent audits never share memory.

A Run is the blackboard the stages hand work through, plus an append-only event log that
the SSE endpoint replays (clients can reconnect with Last-Event-ID and miss nothing).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

STAGES = ("scout", "ranker", "fixer", "narrator")
RunStatus = Literal["running", "complete", "failed"]
StageStatus = Literal["pending", "running", "complete", "error", "skipped"]


@dataclass
class Run:
    run_id: str
    dataset_name: str
    source_sha256: str
    created_at: datetime
    status: RunStatus = "running"
    error: str | None = None
    stages: dict[str, StageStatus] = field(default_factory=lambda: dict.fromkeys(STAGES, "pending"))
    board: dict[str, Any] = field(default_factory=dict)       # stage outputs
    artifacts: dict[str, bytes] = field(default_factory=dict)  # downloadable files
    events: list[dict] = field(default_factory=list)
    task: asyncio.Task | None = None
    _changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    @property
    def reference_id(self) -> str:
        return f"AUDIT-{self.created_at:%Y%m%d}-{self.run_id[:8].upper()}"

    @property
    def finished(self) -> bool:
        return self.status != "running"

    async def emit(self, event: dict) -> None:
        async with self._changed:
            self.events.append({**event, "id": len(self.events) + 1})
            self._changed.notify_all()

    async def set_stage(self, stage: str, status: StageStatus, **extra: Any) -> None:
        self.stages[stage] = status
        await self.emit({"type": "stage", "stage": stage, "status": status, **extra})

    async def finish(self, status: RunStatus, error: str | None = None) -> None:
        self.status, self.error = status, error
        await self.emit({"type": "run", "status": status, "message": error})

    async def follow(self, after_id: int = 0, heartbeat_s: float = 15.0) -> AsyncIterator[dict | None]:
        """Yield events after `after_id` until the run finishes. Yields None as a heartbeat."""
        cursor = after_id
        while True:
            async with self._changed:
                if cursor >= len(self.events) and not self.finished:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._changed.wait(), timeout=heartbeat_s)
                pending = self.events[cursor:]
            if not pending:
                if self.finished:
                    return
                yield None
                continue
            for event in pending:
                cursor = event["id"]
                yield event

    def public(self) -> dict:
        return {
            "run_id": self.run_id,
            "reference_id": self.reference_id,
            "dataset_name": self.dataset_name,
            "source_sha256": self.source_sha256,
            "created_at": self.created_at.isoformat(),
            "status": self.status,
            "error": self.error,
            "stages": self.stages,
            "artifacts": sorted(self.artifacts),
        }


def sse_frame(event: dict | None) -> str:
    """Frame one SSE message. None becomes a comment line, which keeps proxies from idling out."""
    if event is None:
        return ": keep-alive\n\n"
    return f"id: {event['id']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"


class RunStore:
    """Bounded in-process store. Oldest finished runs are evicted first."""

    def __init__(self, max_runs: int) -> None:
        self.max_runs = max_runs
        self._runs: OrderedDict[str, Run] = OrderedDict()

    def create(self, dataset_name: str, source_sha256: str) -> Run:
        self._evict()
        run = Run(run_id=secrets.token_hex(16), dataset_name=dataset_name,
                  source_sha256=source_sha256, created_at=datetime.now(UTC))
        self._runs[run.run_id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def active(self) -> list[Run]:
        return [r for r in self._runs.values() if not r.finished]

    def _evict(self) -> None:
        while len(self._runs) >= self.max_runs:
            victim = next((rid for rid, r in self._runs.items() if r.finished), None)
            if victim is None:
                raise RuntimeError("too many audits in progress")
            del self._runs[victim]
