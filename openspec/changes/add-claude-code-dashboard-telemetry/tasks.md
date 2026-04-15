## 1. Collector and runtime

- [x] 1.1 Add OTEL collector config for Claude Code metrics intake and Prometheus export
- [x] 1.2 Add collector service to compose files
- [x] 1.3 Add app config for telemetry scrape settings, defaulting runtime ingestion off

## 2. Backend

- [x] 2.1 Add telemetry tables and additive migration
- [x] 2.2 Add scrape parser and normalization logic
- [x] 2.3 Add background ingestion and retention scheduler
- [x] 2.4 Extend dashboard overview response with Claude Code data
- [x] 2.5 Add persisted settings flag for Claude dashboard visibility, defaulting the UI off

## 3. Frontend

- [x] 3.1 Extend settings schemas and add a toggle for the Claude dashboard section
- [x] 3.2 Extend dashboard schemas and add the Claude Code section
- [x] 3.3 Hide the section completely when the feature flag is off

## 4. Verification

- [x] 4.1 Add backend tests
- [x] 4.2 Add frontend tests
- [x] 4.3 Run targeted backend and frontend suites
