# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`codex-lb` is a FastAPI-based load balancer and proxy for ChatGPT/Codex accounts. It pools multiple accounts, tracks usage, manages API keys, and serves a React dashboard — all through OpenAI-compatible endpoints (`/v1`, `/backend-api/codex`).

**Ports:** dashboard on `:2455`, metrics/internal on `:1455`.

## Development Commands

### Backend (Python / FastAPI)

```bash
# Install deps
uv sync --all-extras

# Run server (dev)
uv run codex-lb --host 0.0.0.0 --port 2455

# Run all tests
uv run pytest

# Run a specific test file
uv run pytest tests/integration/test_proxy_responses.py -v

# Run by marker
uv run pytest -m integration
uv run pytest -m e2e
uv run pytest -m unit

# Lint
uv run ruff check .
uv run ruff format .

# Type check
uv run ty check

# DB migrations (Alembic via custom wrapper)
uv run codex-lb-db upgrade head
uv run codex-lb-db revision --autogenerate -m "describe change"
```

### Frontend (React / Vite / Bun)

```bash
cd frontend

bun install
bun run dev        # dev server
bun run build      # production build (output goes to app/static/)
bun run typecheck  # tsc check
bun run lint       # eslint
bun run test       # vitest
```

> The backend serves the built frontend from `app/static/`. Run `bun run build` before testing the full stack locally.

## Architecture

### Backend structure

```
app/
├── main.py             # FastAPI app factory (create_app), lifespan, middleware stack
├── cli.py              # Entrypoint — uvicorn wrapper
├── dependencies.py     # FastAPI DI providers (get_session, per-module *Context)
├── core/               # Shared infrastructure (no domain logic)
│   ├── auth/           # Dashboard auth: password, TOTP, API key validation
│   ├── balancer/       # Rendezvous-hash balancer (account selection algorithm)
│   ├── clients/        # Upstream HTTP/WebSocket clients, OAuth, usage fetching
│   ├── config/         # Pydantic settings (env-driven), settings cache
│   ├── middleware/     # Request ID, decompression, API firewall, dashboard auth proxy, bulkhead, backpressure
│   ├── openai/         # OpenAI protocol types: request/response parsing, model registry, chat↔responses coercion
│   ├── rate_limiter/   # DB-backed per-key rate limiting
│   ├── resilience/     # Circuit breaker, bulkhead, retry budget, memory monitor, graceful degradation
│   ├── scheduling/     # Leader election (multi-replica), background task scheduler
│   ├── tracing/        # OpenTelemetry setup
│   └── usage/          # Usage models, quota, depletion detection, pricing
├── db/
│   ├── models.py       # SQLAlchemy ORM models (all tables)
│   ├── session.py      # Async session factory, SQLite/Postgres support
│   ├── migrate.py      # Alembic migration runner + CLI
│   └── alembic/        # Migration scripts (named YYYYMMDD_HHMMSS_*)
└── modules/            # Feature modules — each follows api/service/repository/schemas layout
    ├── proxy/          # Core proxy logic: load balancer, request routing, WebSocket bridge, rate limiting
    ├── accounts/       # Account CRUD, pause/reactivate, OAuth import
    ├── api_keys/       # API key lifecycle, limits, usage reservations
    ├── usage/          # Usage aggregation, dashboard summary
    ├── dashboard/      # Dashboard overview API
    ├── dashboard_auth/ # Password + TOTP login/setup/verify endpoints
    ├── request_logs/   # Request log storage and query
    ├── settings/       # Runtime settings (routing strategy, stream transport, etc.)
    ├── firewall/       # IP allowlist management
    ├── sticky_sessions/# Session→account affinity
    ├── audit/          # Audit trail
    ├── health/         # /health/startup, /health/liveness, /health/readiness
    └── oauth/          # Device OAuth flow for account import
```

### Module conventions

Every `app/modules/<feature>/` follows:
- `api.py` — FastAPI `APIRouter` with route definitions
- `service.py` — business logic, no direct DB access
- `repository.py` — `AsyncSession`-scoped DB queries
- `schemas.py` — Pydantic request/response models

DI wires these together in `app/dependencies.py` via `*Context` dataclasses. Never share sessions across requests or bypass the DI chain.

### Request flow (proxy)

```
Client → Middleware stack → proxy/api.py → ProxyService → LoadBalancer.select_account()
       → upstream HTTP/WebSocket (core/clients/proxy.py or proxy_websocket.py)
       → usage tracking → response stream back to client
```

The `LoadBalancer` lives in `app/modules/proxy/load_balancer.py` and uses rendezvous hashing (`core/balancer/`) combined with quota/circuit-breaker state to pick accounts. Sticky sessions are respected before the hash is evaluated.

### Database

Supports **SQLite** (default, file at `/var/lib/codex-lb/data.db`) and **PostgreSQL** (set `DATABASE_URL`). SQLAlchemy async with Alembic migrations. Migration files are date-prefixed: `YYYYMMDD_HHMMSS_<name>.py`.

### Frontend

React 19 + Vite + Bun. Source in `frontend/src/`, features organized under `frontend/src/features/`. Built output lands in `app/static/` and is served by the FastAPI SPA fallback route.

## Code Conventions

See `.agents/skills/project-conventions/conventions.md` for the full reference. Key rules:
- Strict typing; use Pydantic models at API boundaries, dataclasses internally.
- No speculative fallbacks (`os.getenv("A") or os.getenv("B")`); fail fast.
- `core/` = reusable infrastructure, `modules/` = domain features — don't mix.
- ISO 8601 strings for all datetime values in API responses (not epoch).
- Tests assert public behavior (API responses, service outputs), not internals.

## Testing Strategy

Write **integration** and **e2e** tests; avoid unit tests except for pure algorithmic logic with complex edge cases. Tests use real DB sessions (SQLite in-memory), real HTTP via `httpx.AsyncClient`, and real async flows — no mocking of internal components.

```bash
# Single integration test
uv run pytest tests/integration/test_proxy_responses.py::test_name -v

# E2E tests (require live upstream or mock)
uv run pytest -m e2e -v
```

## Git & PR Conventions

Conventional Commits format: `<type>(<scope>): <description>`
Types: `feat`, `fix`, `docs`, `refactor`, `chore`, `test`, `ci`, `perf`, `build`
Breaking changes: append `!` — e.g. `feat(api)!: remove v1 endpoints`

Branch naming: `feature/`, `fix/`, `chore/` prefixes.

# AGENTS

## Environment

- Python: .venv/bin/python (uv, CPython 3.13.3)
- GitHub auth for git/API is available via env vars: `GITHUB_USER`, `GITHUB_TOKEN` (PAT). Do not hardcode or commit tokens.
- For authenticated git over HTTPS in automation, use: `https://x-access-token:${GITHUB_TOKEN}@github.com/<owner>/<repo>.git`

## Code Conventions

The `/project-conventions` skill is auto-activated on code edits (PreToolUse guard).

| Convention | Location | When |
|-----------|----------|------|
| Code Conventions (Full) | `/project-conventions` skill | On code edit (auto-enforced) |
| Git Workflow | `.agents/conventions/git-workflow.md` | Commit / PR |

## Workflow (OpenSpec-first)

This repo uses **OpenSpec as the primary workflow and SSOT** for change-driven development.

### How to work (default)

1) Find the relevant spec(s) in `openspec/specs/**` and treat them as source-of-truth.
2) If the work changes behavior, requirements, contracts, or schema: create an OpenSpec change in `openspec/changes/**` first (proposal -> tasks).
3) Implement the tasks; keep code + specs in sync (update `spec.md` as needed).
4) Validate specs locally: `openspec validate --specs`
5) When done: verify + archive the change (do not archive unverified changes).

### Source of Truth

- **Specs/Design/Tasks (SSOT)**: `openspec/`
  - Active changes: `openspec/changes/<change>/`
  - Main specs: `openspec/specs/<capability>/spec.md`
  - Archived changes: `openspec/changes/archive/YYYY-MM-DD-<change>/`

## Documentation & Release Notes

- **Do not add/update feature or behavior documentation under `docs/`**. Use OpenSpec context docs under `openspec/specs/<capability>/context.md` (or change-level context under `openspec/changes/<change>/context.md`) as the SSOT.
- **Do not edit `CHANGELOG.md` directly.** Leave changelog updates to the release process; record change notes in OpenSpec artifacts instead.

### Documentation Model (Spec + Context)

- `spec.md` is the **normative SSOT** and should contain only testable requirements.
- Use `openspec/specs/<capability>/context.md` for **free-form context** (purpose, rationale, examples, ops notes).
- If context grows, split into `overview.md`, `rationale.md`, `examples.md`, or `ops.md` within the same capability folder.
- Change-level notes live in `openspec/changes/<change>/context.md` or `notes.md`, then **sync stable context** back into the main context docs.

Prompting cue (use when writing docs):
"Keep `spec.md` strictly for requirements. Add/update `context.md` with purpose, decisions, constraints, failure modes, and at least one concrete example."

### Commands (recommended)

- Start a change: `/opsx:new <kebab-case>`
- Create artifacts (step): `/opsx:continue <change>`
- Create artifacts (fast): `/opsx:ff <change>`
- Implement tasks: `/opsx:apply <change>`
- Verify before archive: `/opsx:verify <change>`
- Sync delta specs → main specs: `/opsx:sync <change>`
- Archive: `/opsx:archive <change>`

## Contributing & Merge Gates

When authoring or merging a PR (as a human contributor, a collaborator,
or an AI assistant acting on behalf of either), the binding workflow is
in [`.github/CONTRIBUTING.md`](.github/CONTRIBUTING.md). The sections
an AI assistant most often needs are:

- [Merge gates](.github/CONTRIBUTING.md#merge-gates) — CI green +
  `@codex review` clean (or findings addressed) + `mergeable=CLEAN` +
  OpenSpec change folder for behavior changes + `Fixes #N` /
  `Closes #N` for issue cover.
- [Collaborator rules](.github/CONTRIBUTING.md#collaborator-rules) —
  no self-merge by default; large PRs get split (≈1-concern per PR,
  ~800 net lines / scoped capability ceiling).
- [Bus factor escape hatch](.github/CONTRIBUTING.md#bus-factor-escape-hatch)
  — self-merge allowed after **14 days** with all gates met and a
  comment invoking the clause.

An assistant preparing a merge MUST verify the gates against the
actual GitHub state (status check rollup, codex review submissions,
`mergeable` field) rather than asserting them from local history.
Local `uv run pytest` / `uv run ruff` / `codex review --base origin/main`
are encouraged but not substitutes for the cloud gates.
