# Design

## Data Flow

Claude Code sends OTLP metrics to an OpenTelemetry collector. The collector exports a Prometheus scrape surface. `codex-lb` scrapes that surface periodically only when telemetry ingestion is enabled, converts cumulative counter values into minute-bucket deltas, stores those deltas in Postgres, and serves pre-aggregated dashboard views from the database.

## Storage

Phase 1 uses:

- a singleton scrape-state row for the most recent cumulative counters
- a minute-bucket table for interval deltas

This supports reliable counter deltas, simple retention cleanup, and efficient timeframe aggregation.

## UI Control

The feature is controlled by a new persisted dashboard setting. When disabled, the dashboard returns `claudeCode: null` and the frontend does not render the Claude Code section, restoring the original dashboard layout.

## Operational Safety

- runtime ingestion is disabled by default
- the collector is optional via compose profile
- the migration is additive and can be applied explicitly by operators
- auth and proxy request handling do not depend on Claude telemetry availability
