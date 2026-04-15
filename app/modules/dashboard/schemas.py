from __future__ import annotations

from datetime import datetime
from typing import List, Literal

from pydantic import Field

from app.modules.accounts.schemas import AccountSummary
from app.modules.shared.schemas import DashboardModel
from app.modules.usage.schemas import MetricsTrends, TrendPoint, UsageWindow, UsageWindowResponse

DashboardOverviewTimeframeKey = Literal["1d", "7d", "30d"]


class DashboardOverviewTimeframe(DashboardModel):
    key: DashboardOverviewTimeframeKey
    window_minutes: int = Field(alias="windowMinutes")
    bucket_seconds: int = Field(alias="bucketSeconds")
    bucket_count: int = Field(alias="bucketCount")


class DashboardUsageCost(DashboardModel):
    currency: str
    total_usd: float = Field(alias="totalUsd")


class DashboardUsageMetrics(DashboardModel):
    requests: int | None = None
    tokens: int | None = None
    cached_input_tokens: int | None = Field(default=None, alias="cachedInputTokens")
    error_rate: float | None = Field(default=None, alias="errorRate")
    error_count: int | None = Field(default=None, alias="errorCount")
    top_error: str | None = None


class ClaudeCodeSummary(DashboardModel):
    sessions: int
    cost_usd: float = Field(alias="costUsd")
    tokens: int
    active_time_seconds: float = Field(alias="activeTimeSeconds")
    lines_added: int = Field(alias="linesAdded")
    lines_removed: int = Field(alias="linesRemoved")
    commits: int
    pull_requests: int = Field(alias="pullRequests")


class ClaudeCodeTrends(DashboardModel):
    sessions: list["TrendPoint"] = Field(default_factory=list)
    cost: list["TrendPoint"] = Field(default_factory=list)
    tokens: list["TrendPoint"] = Field(default_factory=list)
    active_time: list["TrendPoint"] = Field(default_factory=list, alias="activeTime")
    lines_changed: list["TrendPoint"] = Field(default_factory=list, alias="linesChanged")
    commits: list["TrendPoint"] = Field(default_factory=list)
    pull_requests: list["TrendPoint"] = Field(default_factory=list, alias="pullRequests")


class ClaudeCodeOverview(DashboardModel):
    last_sync_at: datetime | None = Field(default=None, alias="lastSyncAt")
    summary: ClaudeCodeSummary
    trends: ClaudeCodeTrends


class DashboardOverviewSummary(DashboardModel):
    primary_window: UsageWindow
    secondary_window: UsageWindow | None = None
    cost: DashboardUsageCost
    metrics: DashboardUsageMetrics | None = None


class DashboardUsageWindows(DashboardModel):
    primary: UsageWindowResponse
    secondary: UsageWindowResponse | None = None


class DepletionResponse(DashboardModel):
    risk: float
    risk_level: str  # "safe" | "warning" | "danger" | "critical"
    burn_rate: float
    safe_usage_percent: float
    projected_exhaustion_at: datetime | None = None
    seconds_until_exhaustion: float | None = None


class DashboardOverviewResponse(DashboardModel):
    last_sync_at: datetime | None = None
    timeframe: DashboardOverviewTimeframe
    accounts: List[AccountSummary] = Field(default_factory=list)
    summary: DashboardOverviewSummary
    windows: DashboardUsageWindows
    trends: MetricsTrends
    depletion_primary: DepletionResponse | None = None
    depletion_secondary: DepletionResponse | None = None
    claude_code: ClaudeCodeOverview | None = Field(default=None, alias="claudeCode")
