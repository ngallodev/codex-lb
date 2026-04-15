from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ClaudeCodeCounterSnapshot:
    sessions_count: float = 0.0
    cost_usage_usd: float = 0.0
    token_input: float = 0.0
    token_output: float = 0.0
    token_cache_read: float = 0.0
    token_cache_creation: float = 0.0
    active_time_user_seconds: float = 0.0
    active_time_cli_seconds: float = 0.0
    lines_added: float = 0.0
    lines_removed: float = 0.0
    commits_count: float = 0.0
    pull_requests_count: float = 0.0

    def delta_from(self, previous: "ClaudeCodeCounterSnapshot") -> "ClaudeCodeCounterSnapshot":
        return ClaudeCodeCounterSnapshot(
            sessions_count=max(0.0, self.sessions_count - previous.sessions_count),
            cost_usage_usd=max(0.0, self.cost_usage_usd - previous.cost_usage_usd),
            token_input=max(0.0, self.token_input - previous.token_input),
            token_output=max(0.0, self.token_output - previous.token_output),
            token_cache_read=max(0.0, self.token_cache_read - previous.token_cache_read),
            token_cache_creation=max(0.0, self.token_cache_creation - previous.token_cache_creation),
            active_time_user_seconds=max(0.0, self.active_time_user_seconds - previous.active_time_user_seconds),
            active_time_cli_seconds=max(0.0, self.active_time_cli_seconds - previous.active_time_cli_seconds),
            lines_added=max(0.0, self.lines_added - previous.lines_added),
            lines_removed=max(0.0, self.lines_removed - previous.lines_removed),
            commits_count=max(0.0, self.commits_count - previous.commits_count),
            pull_requests_count=max(0.0, self.pull_requests_count - previous.pull_requests_count),
        )

    def is_zero(self) -> bool:
        return (
            self.sessions_count == 0.0
            and self.cost_usage_usd == 0.0
            and self.token_input == 0.0
            and self.token_output == 0.0
            and self.token_cache_read == 0.0
            and self.token_cache_creation == 0.0
            and self.active_time_user_seconds == 0.0
            and self.active_time_cli_seconds == 0.0
            and self.lines_added == 0.0
            and self.lines_removed == 0.0
            and self.commits_count == 0.0
            and self.pull_requests_count == 0.0
        )


@dataclass(frozen=True, slots=True)
class ClaudeCodeTelemetrySummary:
    sessions: int
    cost_usd: float
    tokens: int
    active_time_seconds: float
    lines_added: int
    lines_removed: int
    commits: int
    pull_requests: int


@dataclass(frozen=True, slots=True)
class ClaudeCodeTrendBucket:
    bucket_start: datetime
    sessions: float
    cost_usd: float
    tokens: float
    active_time_seconds: float
    lines_changed: float
    commits: float
    pull_requests: float
