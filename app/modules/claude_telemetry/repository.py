from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Integer, cast, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClaudeCodeTelemetryBucket, ClaudeCodeTelemetryState
from app.modules.claude_telemetry.schemas import (
    ClaudeCodeCounterSnapshot,
    ClaudeCodeTelemetrySummary,
    ClaudeCodeTrendBucket,
)

_STATE_ID = 1


class ClaudeCodeTelemetryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_state(self) -> ClaudeCodeTelemetryState:
        state = await self._session.get(ClaudeCodeTelemetryState, _STATE_ID)
        if state is not None:
            return state
        state = ClaudeCodeTelemetryState(id=_STATE_ID)
        self._session.add(state)
        await self._session.commit()
        await self._session.refresh(state)
        return state

    async def update_state(self, snapshot: ClaudeCodeCounterSnapshot, scraped_at: datetime) -> None:
        state = await self.get_state()
        state.last_scraped_at = scraped_at
        state.sessions_count = snapshot.sessions_count
        state.cost_usage_usd = snapshot.cost_usage_usd
        state.token_input = snapshot.token_input
        state.token_output = snapshot.token_output
        state.token_cache_read = snapshot.token_cache_read
        state.token_cache_creation = snapshot.token_cache_creation
        state.active_time_user_seconds = snapshot.active_time_user_seconds
        state.active_time_cli_seconds = snapshot.active_time_cli_seconds
        state.lines_added = snapshot.lines_added
        state.lines_removed = snapshot.lines_removed
        state.commits_count = snapshot.commits_count
        state.pull_requests_count = snapshot.pull_requests_count
        await self._session.commit()

    async def latest_snapshot(self) -> ClaudeCodeCounterSnapshot:
        state = await self.get_state()
        return ClaudeCodeCounterSnapshot(
            sessions_count=state.sessions_count,
            cost_usage_usd=state.cost_usage_usd,
            token_input=state.token_input,
            token_output=state.token_output,
            token_cache_read=state.token_cache_read,
            token_cache_creation=state.token_cache_creation,
            active_time_user_seconds=state.active_time_user_seconds,
            active_time_cli_seconds=state.active_time_cli_seconds,
            lines_added=state.lines_added,
            lines_removed=state.lines_removed,
            commits_count=state.commits_count,
            pull_requests_count=state.pull_requests_count,
        )

    async def add_bucket(self, bucket_start: datetime, delta: ClaudeCodeCounterSnapshot, recorded_at: datetime) -> None:
        stmt = select(ClaudeCodeTelemetryBucket).where(ClaudeCodeTelemetryBucket.bucket_start == bucket_start)
        result = await self._session.execute(stmt)
        bucket = result.scalar_one_or_none()
        if bucket is None:
            bucket = ClaudeCodeTelemetryBucket(bucket_start=bucket_start, recorded_at=recorded_at)
            self._session.add(bucket)
        bucket.recorded_at = recorded_at
        bucket.sessions_count += delta.sessions_count
        bucket.cost_usage_usd += delta.cost_usage_usd
        bucket.token_input += delta.token_input
        bucket.token_output += delta.token_output
        bucket.token_cache_read += delta.token_cache_read
        bucket.token_cache_creation += delta.token_cache_creation
        bucket.active_time_user_seconds += delta.active_time_user_seconds
        bucket.active_time_cli_seconds += delta.active_time_cli_seconds
        bucket.lines_added += delta.lines_added
        bucket.lines_removed += delta.lines_removed
        bucket.commits_count += delta.commits_count
        bucket.pull_requests_count += delta.pull_requests_count
        await self._session.commit()

    async def latest_bucket_at(self) -> datetime | None:
        stmt = select(func.max(ClaudeCodeTelemetryBucket.recorded_at))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def prune_before(self, cutoff: datetime) -> int:
        stmt = delete(ClaudeCodeTelemetryBucket).where(ClaudeCodeTelemetryBucket.bucket_start < cutoff)
        result = await self._session.execute(stmt)
        await self._session.commit()
        return int(result.rowcount or 0)

    async def summarize_since(self, since: datetime) -> ClaudeCodeTelemetrySummary:
        stmt = select(
            func.coalesce(func.sum(ClaudeCodeTelemetryBucket.sessions_count), 0.0),
            func.coalesce(func.sum(ClaudeCodeTelemetryBucket.cost_usage_usd), 0.0),
            func.coalesce(func.sum(ClaudeCodeTelemetryBucket.token_input), 0.0),
            func.coalesce(func.sum(ClaudeCodeTelemetryBucket.token_output), 0.0),
            func.coalesce(func.sum(ClaudeCodeTelemetryBucket.token_cache_read), 0.0),
            func.coalesce(func.sum(ClaudeCodeTelemetryBucket.token_cache_creation), 0.0),
            func.coalesce(func.sum(ClaudeCodeTelemetryBucket.active_time_user_seconds), 0.0),
            func.coalesce(func.sum(ClaudeCodeTelemetryBucket.active_time_cli_seconds), 0.0),
            func.coalesce(func.sum(ClaudeCodeTelemetryBucket.lines_added), 0.0),
            func.coalesce(func.sum(ClaudeCodeTelemetryBucket.lines_removed), 0.0),
            func.coalesce(func.sum(ClaudeCodeTelemetryBucket.commits_count), 0.0),
            func.coalesce(func.sum(ClaudeCodeTelemetryBucket.pull_requests_count), 0.0),
        ).where(ClaudeCodeTelemetryBucket.bucket_start >= since)
        row = (await self._session.execute(stmt)).one()
        tokens = row[2] + row[3] + row[4] + row[5]
        active = row[6] + row[7]
        return ClaudeCodeTelemetrySummary(
            sessions=int(round(row[0])),
            cost_usd=float(row[1]),
            tokens=int(round(tokens)),
            active_time_seconds=float(active),
            lines_added=int(round(row[8])),
            lines_removed=int(round(row[9])),
            commits=int(round(row[10])),
            pull_requests=int(round(row[11])),
        )

    async def trends_by_bucket(self, since: datetime, bucket_seconds: int) -> list[ClaudeCodeTrendBucket]:
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "postgresql":
            bucket_expr = func.floor(func.extract("epoch", ClaudeCodeTelemetryBucket.bucket_start) / bucket_seconds)
            bucket_expr = bucket_expr * bucket_seconds
        else:
            epoch_col = cast(func.strftime("%s", ClaudeCodeTelemetryBucket.bucket_start), Integer)
            bucket_expr = cast(epoch_col / bucket_seconds, Integer) * bucket_seconds
        bucket_col = bucket_expr.label("bucket_epoch")
        stmt = (
            select(
                bucket_col,
                func.coalesce(func.sum(ClaudeCodeTelemetryBucket.sessions_count), 0.0),
                func.coalesce(func.sum(ClaudeCodeTelemetryBucket.cost_usage_usd), 0.0),
                func.coalesce(func.sum(ClaudeCodeTelemetryBucket.token_input), 0.0),
                func.coalesce(func.sum(ClaudeCodeTelemetryBucket.token_output), 0.0),
                func.coalesce(func.sum(ClaudeCodeTelemetryBucket.token_cache_read), 0.0),
                func.coalesce(func.sum(ClaudeCodeTelemetryBucket.token_cache_creation), 0.0),
                func.coalesce(func.sum(ClaudeCodeTelemetryBucket.active_time_user_seconds), 0.0),
                func.coalesce(func.sum(ClaudeCodeTelemetryBucket.active_time_cli_seconds), 0.0),
                func.coalesce(func.sum(ClaudeCodeTelemetryBucket.lines_added), 0.0),
                func.coalesce(func.sum(ClaudeCodeTelemetryBucket.lines_removed), 0.0),
                func.coalesce(func.sum(ClaudeCodeTelemetryBucket.commits_count), 0.0),
                func.coalesce(func.sum(ClaudeCodeTelemetryBucket.pull_requests_count), 0.0),
            )
            .where(ClaudeCodeTelemetryBucket.bucket_start >= since)
            .group_by(bucket_col)
            .order_by(bucket_col.asc())
        )
        rows = (await self._session.execute(stmt)).all()
        results: list[ClaudeCodeTrendBucket] = []
        for row in rows:
            bucket_start = datetime.fromtimestamp(int(row[0]), tz=UTC).replace(tzinfo=None)
            tokens = float(row[3]) + float(row[4]) + float(row[5]) + float(row[6])
            active = float(row[7]) + float(row[8])
            lines_changed = float(row[9]) + float(row[10])
            results.append(
                ClaudeCodeTrendBucket(
                    bucket_start=bucket_start,
                    sessions=float(row[1]),
                    cost_usd=float(row[2]),
                    tokens=tokens,
                    active_time_seconds=active,
                    lines_changed=lines_changed,
                    commits=float(row[11]),
                    pull_requests=float(row[12]),
                )
            )
        return results
