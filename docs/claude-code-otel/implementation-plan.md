# Claude Code OTEL Dashboard Implementation

## Summary

Add Claude Code CLI OpenTelemetry metrics to the existing dashboard without requiring an external observability backend at read time.

Phase 1 uses an OpenTelemetry collector sidecar to receive Claude Code OTLP metrics and expose them in Prometheus format. `codex-lb` scrapes the collector on an interval, stores normalized global metric deltas in Postgres, and extends the existing dashboard overview response with a new Claude Code section.

The feature ships default-off end to end:

- telemetry ingestion is disabled unless explicitly enabled in config
- the collector is optional via compose profile
- the dashboard section is hidden unless explicitly enabled in settings
- the database migration is additive and available for operators to run when they choose

## Scope

- Metrics only
- Global aggregate only
- 30 day retention
- Existing dashboard page, not a new route
- Settings-backed UI toggle for showing or hiding the Claude Code dashboard section
- Config-backed runtime toggle for enabling or disabling telemetry ingestion
- Explicit migration script, with no new forced migration behavior introduced by this feature

## Architecture

1. Claude Code exports OTLP metrics to `otel-collector`
2. The collector exposes Prometheus metrics
3. `codex-lb` scrapes the collector on a background interval only when telemetry ingestion is enabled
4. The scraper computes deltas from cumulative counters and stores minute buckets
5. Dashboard overview aggregates those buckets for `1d`, `7d`, and `30d`
6. Frontend renders the Claude Code section only when the settings toggle is enabled

## Backend Changes

- Add collector scrape settings
- Add telemetry storage tables and migration
- Add a background scheduler for telemetry ingestion and retention cleanup
- Extend dashboard overview response with Claude Code summary and trends
- Add a persisted `show_claude_code_dashboard` dashboard setting
- Keep core auth and proxy flows independent of the telemetry path

## Frontend Changes

- Extend dashboard overview schema with a nullable `claudeCode` object
- Add a Claude Code section to the existing dashboard page
- Add a settings switch to enable or disable the Claude Code dashboard section
- Preserve the original dashboard layout when the toggle is off

## Metrics

Phase 1 stores and displays:

- sessions
- cost
- total tokens
- active time
- lines added
- lines removed
- commits
- pull requests

## Tests

- parser and normalization tests for Prometheus scrape input
- repository and aggregation tests for timeframe rollups
- dashboard overview integration tests
- settings API tests for the new feature flag
- frontend schema, settings toggle, and dashboard rendering tests
