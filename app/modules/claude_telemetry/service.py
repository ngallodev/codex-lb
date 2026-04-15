from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

import aiohttp

from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.modules.claude_telemetry.repository import ClaudeCodeTelemetryRepository
from app.modules.claude_telemetry.schemas import (
    ClaudeCodeCounterSnapshot,
    ClaudeCodeTelemetrySummary,
    ClaudeCodeTrendBucket,
)

logger = logging.getLogger(__name__)

_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\\\.|[^"])*)"')


class ClaudeCodeTelemetryService:
    def __init__(self, repository: ClaudeCodeTelemetryRepository) -> None:
        self._repository = repository

    async def scrape_and_store(self) -> bool:
        settings = get_settings()
        async with aiohttp.ClientSession() as session:
            async with session.get(settings.claude_code_telemetry_scrape_url) as response:
                response.raise_for_status()
                payload = await response.text()
        snapshot = parse_claude_code_prometheus(payload)
        previous = await self._repository.latest_snapshot()
        delta = snapshot.delta_from(previous)
        scraped_at = utcnow()
        if not delta.is_zero():
            bucket_start = scraped_at.replace(second=0, microsecond=0)
            await self._repository.add_bucket(bucket_start, delta, scraped_at)
        await self._repository.update_state(snapshot, scraped_at)
        return True

    async def prune_expired(self) -> int:
        settings = get_settings()
        cutoff = utcnow() - timedelta(days=settings.claude_code_telemetry_retention_days)
        return await self._repository.prune_before(cutoff)

    async def summarize_since(self, since: datetime) -> ClaudeCodeTelemetrySummary:
        return await self._repository.summarize_since(since)

    async def trends_by_bucket(self, since: datetime, bucket_seconds: int) -> list[ClaudeCodeTrendBucket]:
        return await self._repository.trends_by_bucket(since, bucket_seconds)

    async def latest_bucket_at(self) -> datetime | None:
        return await self._repository.latest_bucket_at()


def parse_claude_code_prometheus(payload: str) -> ClaudeCodeCounterSnapshot:
    values: dict[str, float] = {
        "sessions_count": 0.0,
        "cost_usage_usd": 0.0,
        "token_input": 0.0,
        "token_output": 0.0,
        "token_cache_read": 0.0,
        "token_cache_creation": 0.0,
        "active_time_user_seconds": 0.0,
        "active_time_cli_seconds": 0.0,
        "lines_added": 0.0,
        "lines_removed": 0.0,
        "commits_count": 0.0,
        "pull_requests_count": 0.0,
    }
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        metric_part, _, value_part = line.partition(" ")
        if not value_part:
            continue
        try:
            value = float(value_part.strip())
        except ValueError:
            continue
        name, labels = _parse_metric(metric_part)
        key = _metric_key(name, labels)
        if key is None:
            continue
        values[key] += value
    return ClaudeCodeCounterSnapshot(**values)


def _parse_metric(metric_part: str) -> tuple[str, dict[str, str]]:
    labels: dict[str, str] = {}
    if "{" not in metric_part:
        return metric_part.replace(".", "_"), labels
    name, label_blob = metric_part.split("{", 1)
    label_blob = label_blob.rstrip("}")
    for match in _LABEL_RE.finditer(label_blob):
        labels[match.group(1)] = match.group(2)
    return name.replace(".", "_"), labels


def _metric_key(name: str, labels: dict[str, str]) -> str | None:
    if name.startswith("claude_code_session_count"):
        return "sessions_count"
    if name.startswith("claude_code_cost_usage"):
        return "cost_usage_usd"
    if name.startswith("claude_code_commit_count"):
        return "commits_count"
    if name.startswith("claude_code_pull_request_count"):
        return "pull_requests_count"
    if name.startswith("claude_code_lines_of_code_count"):
        line_type = labels.get("type", "")
        if line_type == "added":
            return "lines_added"
        if line_type == "removed":
            return "lines_removed"
        return None
    if name.startswith("claude_code_token_usage"):
        token_type = labels.get("type", "")
        if token_type == "input":
            return "token_input"
        if token_type == "output":
            return "token_output"
        if token_type == "cacheRead":
            return "token_cache_read"
        if token_type == "cacheCreation":
            return "token_cache_creation"
        return None
    if name.startswith("claude_code_active_time_total") or name.startswith("claude_code_active_time"):
        activity_type = labels.get("type", "")
        if activity_type == "user":
            return "active_time_user_seconds"
        if activity_type == "cli":
            return "active_time_cli_seconds"
        return None
    return None
