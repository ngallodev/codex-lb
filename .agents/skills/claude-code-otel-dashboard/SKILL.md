---
name: claude-code-otel-dashboard
description: Implement Claude Code OTEL collector ingestion, dashboard aggregation, and dashboard UI integration for codex-lb.
---

# Claude Code OTEL Dashboard

Use this skill when implementing or modifying the Claude Code telemetry pipeline and dashboard integration in this repo.

## Responsibilities

- Collector integration and runtime wiring
- Claude telemetry scraping and normalization
- Dashboard aggregation and response contracts
- Dashboard settings toggle and UI rendering

## Preferred agent split

- `backend_ingestion`: ingestion, storage, scheduler, API contracts, backend tests
- `frontend_dashboard`: frontend schema, dashboard section, settings toggle, frontend tests
- `collector_ops`: collector config, compose wiring, operator docs

## Guardrails

- Keep the feature reversible with a persisted settings toggle.
- Keep the existing dashboard route and query flow.
- Use DB-backed rollups rather than read-time external telemetry queries.
- Preserve `1d`, `7d`, and `30d` timeframe behavior.
