from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
from dataclasses import dataclass, field
from typing import Protocol, cast

from app.core.config.settings import get_settings
from app.db.session import get_background_session
from app.modules.claude_telemetry.repository import ClaudeCodeTelemetryRepository
from app.modules.claude_telemetry.service import ClaudeCodeTelemetryService

logger = logging.getLogger(__name__)


class _LeaderElectionLike(Protocol):
    async def try_acquire(self) -> bool: ...


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


@dataclass(slots=True)
class ClaudeCodeTelemetryScheduler:
    interval_seconds: int
    enabled: bool
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            await self._refresh_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _refresh_once(self) -> None:
        if not await _get_leader_election().try_acquire():
            return
        async with self._lock:
            try:
                async with get_background_session() as session:
                    repository = ClaudeCodeTelemetryRepository(session)
                    service = ClaudeCodeTelemetryService(repository)
                    await service.scrape_and_store()
                    await service.prune_expired()
            except Exception:
                logger.exception("Claude Code telemetry refresh loop failed")


def build_claude_code_telemetry_scheduler() -> ClaudeCodeTelemetryScheduler:
    settings = get_settings()
    return ClaudeCodeTelemetryScheduler(
        interval_seconds=settings.claude_code_telemetry_scrape_interval_seconds,
        enabled=settings.claude_code_telemetry_enabled,
    )
