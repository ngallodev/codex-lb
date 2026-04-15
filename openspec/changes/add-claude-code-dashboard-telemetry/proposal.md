# Add Claude Code Dashboard Telemetry

## Why

Operators need visibility into Claude Code usage, cost, and productivity metrics from the existing `codex-lb` dashboard.

## What Changes

- Add a collector-backed ingestion pipeline for Claude Code OTEL metrics
- Store normalized telemetry rollups in the application database
- Extend the dashboard overview response with Claude Code summary and trends
- Add a settings-backed toggle to show or hide the Claude Code dashboard section
- Keep the full feature optional and PR-safe by shipping it default-off at runtime and in the UI

## Impact

- new database tables and migration
- new background scheduler
- compose and collector config additions
- dashboard backend and frontend contract changes
- no new hard dependency from auth/proxy startup onto Claude telemetry
