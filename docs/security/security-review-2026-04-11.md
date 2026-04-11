# Security Review — codex-lb

**Date:** 2026-04-11
**Reviewer:** ngallodev (Claude Code assisted)
**Repo:** https://github.com/Soju06/codex-lb → fork: https://github.com/ngallodev/codex-lb
**Branch:** security/initial-review
**Methodology:** Full codebase analysis via codebase-memory-mcp knowledge graph (11,903 nodes / 35,254 edges). Every file, class, library, and execution path was traced through the graph before any conclusion was drawn. Source snippets were read directly via `get_code_snippet` for all security-relevant functions.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture Summary](#2-architecture-summary)
3. [Security Surface Map](#3-security-surface-map)
4. [Methodology](#4-methodology)
5. [Findings — Medium Severity](#5-findings--medium-severity)
6. [Findings — Low Severity](#6-findings--low-severity)
7. [Passed Checks — No Vulnerabilities Found](#7-passed-checks--no-vulnerabilities-found)
8. [Dependency Analysis](#8-dependency-analysis)
9. [Architecture Decision Records Referenced](#9-architecture-decision-records-referenced)
10. [Recommendations Summary](#10-recommendations-summary)

---

## 1. Project Overview

`codex-lb` is a self-hosted FastAPI reverse proxy / load balancer that sits in front of the OpenAI/Codex API. It pools multiple upstream accounts, enforces per-key and per-account quotas and rate limits, and exposes a React-based management dashboard.

**Key capabilities:**

- Multi-account rendezvous-hash load balancing with quota and circuit-breaker state
- Per-API-key model restrictions, usage quotas, and reservations
- Dashboard with password + TOTP authentication (or trusted-header proxy auth)
- IP firewall with per-path allowlists
- OAuth PKCE flow for upstream account credential import
- WebSocket proxying for the Responses API
- Sticky sessions, audit logging, usage analytics
- Kubernetes Helm chart deployment

**Ports:** Dashboard on `:2455`, metrics/internal on `:1455`.

**Stack:**

| Component | Technology |
|---|---|
| Runtime | Python 3.13.3, FastAPI 0.128+, uvicorn |
| DB | SQLAlchemy 2.0 async — SQLite (default) or PostgreSQL |
| Migrations | Alembic (date-prefixed, custom CLI wrapper) |
| Crypto | `cryptography` 46+ (Fernet/AES-128-CBC+HMAC-SHA256), `bcrypt` 4.3+ |
| TOTP | `pyotp` 2.9+ |
| HTTP client | `aiohttp` 3.13+, `httpx` 0.28+ |
| Frontend | React 19, Vite, Bun |
| Validation | Pydantic v2 |
| Observability | OpenTelemetry (fastapi, aiohttp, sqlalchemy instrumentation) |

---

## 2. Architecture Summary

### Backend directory structure

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
│   ├── openai/         # OpenAI protocol types: request/response parsing, model registry, chat<->responses coercion
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
    ├── sticky_sessions/# Session->account affinity
    ├── audit/          # Audit trail
    ├── health/         # /health/startup, /health/liveness, /health/readiness
    └── oauth/          # Device OAuth flow for account import
```

### Middleware stack (applied bottom-up at startup)

1. `RequestIdMiddleware` — injects `X-Request-ID` per request
2. `RequestDecompressionMiddleware` — decompresses gzip/deflate/zstd bodies, hard size cap via `max_decompressed_body_bytes`
3. `ApiFirewallMiddleware` — IP allowlist check against all `/v1/*` and `/backend-api/*` paths, fires before auth
4. `DashboardAuthProxyHeaderSanitizerMiddleware` — strips the configured proxy-auth header from untrusted TCP peers
5. `MetricsMiddleware` / `InFlightMiddleware` — Prometheus metrics, in-flight request counting

Route-level auth (FastAPI dependencies) sits above all middleware:
- `validate_proxy_api_key_authorization` — proxy paths
- `DashboardAuthService` session/TOTP validation — dashboard paths

### Request flow (proxy)

```
Client
  -> Middleware stack (firewall, decompression, header sanitization)
  -> proxy/api.py route handler
  -> ProxyService
  -> LoadBalancer.select_account()   (rendezvous hash + quota/circuit-breaker state)
  -> core/clients/proxy.py           (upstream HTTP or WebSocket)
  -> usage tracking
  -> response stream back to client
```

### Route inventory (161 total routes)

Key route groups:

| Prefix | Purpose | Auth required |
|---|---|---|
| `/v1/*`, `/backend-api/codex/*` | OpenAI-compatible proxy | Bearer API key |
| `/api/dashboard-auth/*` | Login, TOTP, session management | Varies per endpoint |
| `/api/api-keys/*` | API key lifecycle | Dashboard session |
| `/api/accounts/*` | Account management | Dashboard session |
| `/api/firewall/*` | IP allowlist management | Dashboard session |
| `/api/settings` | Runtime settings | Dashboard session |
| `/api/audit-logs` | Audit trail | Dashboard session |
| `/api/request-logs` | Request log query | Dashboard session |
| `/api/oauth/*` | OAuth device flow | Dashboard session |
| `/health`, `/health/live`, `/health/ready` | Health probes | None |
| `/api/codex/usage` | Codex usage endpoint | API key |

---

## 3. Security Surface Map

### Authentication surfaces

| Surface | Mechanism | Implementation file |
|---|---|---|
| Proxy API key | SHA-256 hash lookup, Bearer token | `app/core/auth/dependencies.py` |
| Dashboard password | bcrypt verify | `app/modules/dashboard_auth/service.py` |
| Dashboard TOTP | pyotp + `hmac.compare_digest` + replay protection | `app/core/auth/totp.py` |
| Dashboard session | Fernet-encrypted stateless token in HttpOnly cookie | `app/modules/dashboard_auth/service.py` |
| Bootstrap token | SHA-256 hash + `compare_digest` | `app/core/bootstrap.py` |
| Trusted-header proxy auth | Upstream proxy header (operator-configured) | `app/core/auth/dashboard_mode.py` |
| OAuth PKCE | PKCE S256 + state param | `app/core/clients/oauth.py` |

### Cryptographic primitives in use

| Purpose | Primitive | Location |
|---|---|---|
| Session token encryption | Fernet (AES-128-CBC + HMAC-SHA256) | `app/core/crypto.py` |
| Upstream token storage | Fernet | `app/core/crypto.py` |
| Bootstrap token storage | Fernet (encrypted) + SHA-256 (hash) | `app/core/bootstrap.py` |
| API key storage | SHA-256 hexdigest | `app/modules/api_keys/service.py` |
| Password storage | bcrypt (with gensalt) | `app/modules/dashboard_auth/service.py` |
| TOTP comparison | `hmac.compare_digest` | `app/core/auth/totp.py` |
| Bootstrap comparison | `hmac.compare_digest` | `app/core/bootstrap.py` |
| PKCE challenge | SHA-256 / S256 | `app/core/clients/oauth.py` |

---

## 4. Methodology

Analysis was performed using codebase-memory-mcp knowledge graph tools against the fully indexed repository:

- **11,903 nodes** (3,216 functions, 875 methods, 556 classes, 161 routes, 1,107 files, 1,085 modules)
- **35,254 edges** (11,037 CALLS, 10,254 DEFINES, 4,947 USAGE, 1,034 IMPORTS, 63 DECORATES/HANDLES)

Every finding was verified by reading the actual source via `get_code_snippet` before being included. The following query categories were executed:

1. Authentication and authorization paths (all auth middleware and dependencies)
2. Cryptographic operations (all hash, encrypt, decrypt, compare functions)
3. Input handling (decompression, header filtering, URL parsing)
4. Database access patterns (SQL injection surface)
5. External HTTP requests (SSRF surface — image inlining, upstream calls)
6. Session management (creation, validation, deletion)
7. Secret/credential storage and retrieval
8. Frontend XSS surface (`dangerouslySetInnerHTML`, raw HTML rendering)
9. Shell/OS command execution (`subprocess`, `os.system`, `exec`)
10. TLS/SSL configuration (verify flags, certificate checking)
11. Hardcoded credentials in application source
12. Rate limiting and brute-force protection
13. Header injection and forwarding
14. OAuth state/CSRF protection
15. Audit and logging (PII/sensitive data in logs)

---

## 5. Findings — Medium Severity

### M-1: API key hashing uses SHA-256 without a salt or work factor

**File:** `app/modules/api_keys/service.py:849`, `app/core/auth/dependencies.py:71`

**Source read:**
```python
# app/modules/api_keys/service.py:849
def _hash_key(plain_key: str) -> str:
    return sha256(plain_key.encode("utf-8")).hexdigest()

# app/core/auth/dependencies.py:71
async def _validate_api_key_token(token: str) -> ApiKeyData:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cache = get_api_key_cache()
    cached = await cache.get(token_hash)
    ...
```

**Detail:**

API keys are stored as `sha256(plaintext).hexdigest()` with no salt and no key-stretching work factor. The keys themselves are generated with `secrets.token_urlsafe(32)` (>=192 bits of entropy), which means precomputed rainbow tables and offline brute-force are not practical threats in isolation.

However, if the database is compromised (e.g., via SQL injection in a future regression, backup exposure, or insider access), an attacker who also has a candidate key can verify a match in a single SHA-256 operation — microseconds per guess. A work factor of even bcrypt cost 10 would raise that to ~100ms per guess.

The inconsistency is notable: dashboard passwords (which have lower per-value entropy than the randomly generated keys) correctly use bcrypt, while the keys do not.

**Risk context:** Exploitable only after DB compromise AND possession of candidate keys. Not a standalone exploitable vulnerability.

**Recommendation:**
- Short-term: Document the accepted risk explicitly in the ADR.
- Preferred: Switch to PBKDF2-HMAC-SHA256 with a per-key salt (stored alongside the hash). The hot-path cache (`get_api_key_cache`) already absorbs most of the latency cost — the bcrypt/PBKDF2 penalty is only paid on cache miss.

---

### M-2: Stateless sessions — no server-side revocation on credential change

**File:** `app/modules/dashboard_auth/service.py:87` (`DashboardSessionStore`)

**Source read:**
```python
class DashboardSessionStore:
    def create(self, *, password_verified: bool, totp_verified: bool) -> str:
        expires_at = int(time()) + _SESSION_TTL_SECONDS
        payload = json.dumps(
            {"exp": expires_at, "pw": password_verified, "tv": totp_verified},
            separators=(",", ":"),
        )
        return self._get_encryptor().encrypt(payload).decode("ascii")

    def delete(self, session_id: str | None) -> None:
        # Stateless: deletion is handled by clearing the cookie client-side.
        return
```

**Detail:**

Sessions are entirely stateless — the encrypted cookie IS the session. `delete()` is a documented no-op. This means:

1. **Password change** does not invalidate existing sessions. A session token captured before a password change remains valid for up to 12 hours after the change.
2. **Logout** only clears the cookie client-side. If the cookie value was intercepted (e.g., from browser history, server logs, a network capture before TLS, or a misconfigured HTTP proxy), the stolen token can be replayed until expiry.
3. **TOTP disable** does not invalidate sessions that had `tv=true`.
4. There is no mechanism to "log out all sessions" during a security incident.

The 12-hour TTL (`max_age=12 * 60 * 60`) limits the window, but it is not negligible.

**Recommendation (in order of preference):**

1. **Generation counter in DB:** Store a monotonic `session_generation` integer in the settings/auth table. Embed the current generation in the session payload at creation time. On password change, TOTP disable, or explicit "logout all," increment the counter. On session validation, reject tokens whose generation < current. This preserves the stateless property while enabling instant invalidation.

2. **Short-lived nonce table:** Store a per-session nonce (UUID) in a fast-access table (or Redis). `delete()` removes the nonce. Session validation checks the nonce is present. The table only needs one row per active session.

3. **Reduce TTL:** Lowering from 12h to 1-2h shrinks the exploit window at the cost of more frequent re-authentication.

---

### M-3: Single Fernet key — no rotation mechanism

**File:** `app/core/crypto.py:10` (`_get_or_create_key`), `app/core/crypto.py:20` (`TokenEncryptor`)

**Source read:**
```python
def _get_or_create_key(key_file: Path) -> bytes:
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        return key_file.read_bytes()
    key = Fernet.generate_key()
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    return key

class TokenEncryptor:
    def __init__(self, key: bytes | None = None, key_file: Path | None = None) -> None:
        settings = get_settings()
        resolved_file = key_file or settings.encryption_key_file
        resolved_key = key or _get_or_create_key(resolved_file)
        self._fernet = Fernet(resolved_key)
```

**Detail:**

A single Fernet key is used to encrypt all sensitive data at rest:
- Dashboard session tokens
- Upstream OAuth access tokens stored in the accounts table
- The bootstrap token (encrypted cleartext copy)

There is no key versioning, no `MultiFernet` multi-key decryption, and no re-encryption workflow. This means:

1. **No rotation path:** If an operator suspects key compromise, there is no supported way to rotate the key without decrypting and re-encrypting every row in the database.
2. **Blast radius of key compromise:** Any past or present encrypted value in the database becomes readable.
3. **Single point of failure:** The file lives on disk (`chmod 0o600`) adjacent to the process. Container image leaks, misconfigured volume mounts, or host filesystem access all expose the key.

**Recommendation:**

1. Switch from `Fernet(key)` to `MultiFernet([Fernet(new_key), Fernet(old_key)])` during a transition period. Python's `cryptography` library supports this natively — it tries each key in order for decryption, encrypts with the first (current) key only.
2. Add a CLI command (`codex-lb-db rotate-key --new-key-file ...`) that re-encrypts all encrypted DB columns under the new key.
3. Document the key file location, backup procedure, and rotation runbook in ops documentation.

---

### M-4: Bootstrap token stored as SHA-256 hash (no work factor)

**File:** `app/core/bootstrap.py:23`

**Source read:**
```python
def _hash_bootstrap_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()

async def validate_bootstrap_token(submitted_token: str) -> bool:
    manual = _get_manual_bootstrap_token()
    if manual is not None:
        return compare_digest(submitted_token.encode("utf-8"), manual.encode("utf-8"))

    password_hash, _, bootstrap_token_hash = await _get_shared_bootstrap_state()
    if password_hash is not None or bootstrap_token_hash is None:
        return False
    return compare_digest(_hash_bootstrap_token(submitted_token), bootstrap_token_hash)
```

**Detail:**

The auto-generated bootstrap token (`secrets.token_urlsafe(32)`, >=192 bits) is stored as a SHA-256 hash with no salt or work factor. At >=192 bits of entropy this is not practically brute-forceable, and `compare_digest` prevents timing attacks.

However:
1. If a short or low-entropy manual bootstrap token is configured via `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN`, it is compared directly (plain `compare_digest` with no hashing at all for the manual path).
2. The pattern is inconsistent with the rest of the auth surface (passwords use bcrypt).
3. A future change to shorter auto-generated tokens would silently inherit weak protection.

**Recommendation:** Low urgency for auto-generated tokens. For the manual env-var path, consider warning at startup if the token appears low-entropy (< 16 characters). Switching to bcrypt for the stored hash would be consistent and future-safe.

---

## 6. Findings — Low Severity

### L-1: Trusted-header auth mode is misconfiguration-prone

**File:** `app/core/auth/dashboard_mode.py:94` (`_get_trusted_header_auth`), `app/core/middleware/dashboard_auth_proxy.py`

**Source read:**
```python
def _get_trusted_header_auth(request: Request) -> DashboardRequestAuth | None:
    settings = get_settings()
    client_host = request.client.host if request.client else None
    if not client_host or not settings.firewall_trust_proxy_headers:
        return None
    if not _is_trusted_proxy_source(client_host, _trusted_proxy_networks()):
        return None

    raw_actor = request.headers.get(settings.dashboard_auth_proxy_header)
    if raw_actor is None:
        return None

    actor = raw_actor.strip()
    if not actor:
        return None
    return DashboardRequestAuth(mode=DashboardAuthMode.TRUSTED_HEADER, actor=actor)
```

**Detail:**

When `dashboard_auth_mode=trusted_header`, the dashboard grants access to any identity supplied in the configured upstream proxy header. The implementation is correct — the sanitizer middleware strips the header from non-trusted source IPs, settings validation rejects reserved header names, and the source IP must be in `firewall_trusted_proxy_cidrs`.

The risk is entirely operational: if `firewall_trusted_proxy_cidrs` is empty or set to `0.0.0.0/0`, any client that can set an HTTP header gains full dashboard access without any other credential. There is no startup-time guard for this misconfiguration.

**Recommendation:**
- Add a startup warning log (not a hard error, to avoid breaking existing deployments) if `trusted_header` mode is configured with `firewall_trusted_proxy_cidrs` that is empty or contains an excessively broad range (e.g., `0.0.0.0/0` or `::/0`).
- Consider adding this check to settings validation (pydantic validator on the settings model).

---

### L-2: Session cookie uses `samesite=lax` instead of `strict`

**File:** `app/modules/dashboard_auth/api.py:476` (`_set_session_cookie`)

**Source read:**
```python
def _set_session_cookie(response: JSONResponse, session_id: str, request: Request) -> None:
    response.set_cookie(
        key=DASHBOARD_SESSION_COOKIE,
        value=session_id,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=12 * 60 * 60,
        path="/",
    )
```

**Detail:**

`samesite=lax` allows the cookie to be sent on top-level navigations (e.g., a link from another site that navigates to the dashboard). This is not a CSRF vector for state-mutating operations (which are POST/PUT/DELETE and correctly require the session cookie rather than a CSRF token), but it is a slight information-leak risk if any dashboard GET endpoint returns sensitive data and an attacker can trick the admin into clicking a link.

`samesite=strict` would prevent this entirely by only sending the cookie when the origin exactly matches.

**Recommendation:** Change `samesite="lax"` to `samesite="strict"`. The only behavioral difference is that navigating to the dashboard from an external link will require the user to re-authenticate, which is expected behavior for a management dashboard.

---

### L-3: Image inlining — residual DNS TOCTOU window

**File:** `app/core/clients/proxy.py:1413` (`_resolve_safe_image_fetch_target`)

**Source read (key excerpt):**
```python
async def _resolve_safe_image_fetch_target(url: str, *, connect_timeout: float) -> SafeImageFetchTarget | None:
    ...
    if parsed.scheme != "https":
        return None
    if parsed.username or parsed.password:
        return None
    ...
    literal_ip = _parse_ip_literal(host)
    if literal_ip is not None:
        if _is_disallowed_ip(literal_ip):
            return None
        resolved_ips = [literal_ip.compressed]
    else:
        resolved_ips = await _resolve_global_ips(host, timeout_seconds=resolve_timeout)
        if not resolved_ips:
            return None
    request_urls = tuple(_build_pinned_request_url(parsed, resolved_ip) for resolved_ip in resolved_ips)
    ...
```

**Detail:**

The SSRF mitigation for image inlining is strong: HTTPS-only, no embedded credentials, allowlist check, RFC-1918/loopback IP blocking, and DNS-resolution pinning (the request URL is rewritten to the resolved IP, with the hostname passed via `Host:` header).

The residual risk is a narrow DNS rebinding window: between the DNS resolution inside this function and the actual TCP connection establishment, the OS may perform another DNS lookup (if the DNS TTL is very short and a cache entry expires). If an attacker controls DNS and sets TTL=0, they could serve a public IP during validation and a private IP at connection time.

The pinned-URL approach (`_build_pinned_request_url`) substantially closes this window — the connection is established to the IP address, not the hostname. This is a known residual risk in all DNS-pinning-based SSRF mitigations, not a code defect.

**Recommendation:** Document as known residual risk. If the threat model requires defense against sophisticated DNS rebinding, consider a dedicated DNS resolver that enforces the IP check at resolution time and disallows TTL=0 responses.

---

### L-4: Encryption key file creation — startup race condition

**File:** `app/core/crypto.py:10` (`_get_or_create_key`)

**Source read:**
```python
def _get_or_create_key(key_file: Path) -> bytes:
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        return key_file.read_bytes()
    key = Fernet.generate_key()
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    return key
```

**Detail:**

Under concurrent startup (multiple uvicorn workers starting simultaneously on a first install where no key file exists), the TOCTOU window between `key_file.exists()` returning `False` and `key_file.write_bytes(key)` means two workers could each generate different keys. The second `write_bytes` call would silently overwrite the first, leaving workers with different keys in memory until the next restart. This would cause intermittent session and token decryption failures between workers.

Unlikely in practice (the file is stable after first write), but a theoretical race on fresh deployments with pre-forked multi-worker setups.

**Recommendation:** Use an atomic write pattern:

```python
import os, tempfile
def _get_or_create_key(key_file: Path) -> bytes:
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        return key_file.read_bytes()
    key = Fernet.generate_key()
    fd, tmp = tempfile.mkstemp(dir=key_file.parent)
    try:
        os.write(fd, key)
        os.fchmod(fd, 0o600)
        os.close(fd)
        os.rename(tmp, str(key_file))
    except Exception:
        os.unlink(tmp)
        raise
    # Re-read in case another worker won the race
    return key_file.read_bytes()
```

---

## 7. Passed Checks — No Vulnerabilities Found

### SQL Injection — PASS

No raw SQL string construction anywhere in `app/`. All database access goes through SQLAlchemy ORM with parameterized queries. Full search for `execute`, `text()`, and raw string patterns returned only Alembic migration scripts using typed DDL helpers and test fakes with bound parameters.

### Command Injection — PASS

No `subprocess`, `os.system`, `os.popen`, `exec()`, or `eval()` calls in `app/`. Dev tooling scripts under `.agents/hooks/` use subprocess for git operations only and are not part of the runtime application.

### XSS — Frontend — PASS

No `dangerouslySetInnerHTML` anywhere in the React frontend (`frontend/src/`). All user-facing text is rendered through React's default escaping. API error messages are returned as structured JSON and displayed via React components, never injected as raw HTML.

### SSL/TLS Verification — PASS

No `verify=False`, `ssl=False`, `check_hostname=False`, or equivalent TLS-disabling flags anywhere in `app/`. All outbound HTTP connections via `aiohttp` and `httpx` use default TLS verification.

### Hardcoded Secrets — PASS

No hardcoded passwords, API keys, tokens, or encryption keys found in `app/`. Regex search for credential assignment patterns (`password = "..."`, `secret = "..."`, etc.) returned zero matches in application code. All credentials are environment-variable-sourced via Pydantic settings.

### Header Injection to Upstream — PASS

`filter_inbound_headers` (`app/core/clients/proxy.py:342`) strips `Authorization`, `x-api-key`, and other identity headers from client requests before forwarding to the upstream OpenAI API. The upstream `Authorization` header is always set to the upstream account's own key, never passed through from the client.

```python
def filter_inbound_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if not _should_drop_inbound_header(key)}
```

Tested by `test_filter_inbound_headers_strips_auth_and_account` and `test_filter_inbound_headers_strips_proxy_identity_headers`.

### TOTP Replay Attacks — PASS

`verify_totp_code` (`app/core/auth/totp.py:32`) tracks `last_verified_step` and refuses to accept a code for the same or earlier time step, preventing replay within the same TOTP window. Uses `hmac.compare_digest` throughout.

```python
for offset in range(-window, window + 1):
    step = current_step + offset
    if last_verified_step is not None and step <= last_verified_step:
        continue
    expected = totp.at(step * _TOTP_PERIOD_SECONDS)
    if hmac.compare_digest(expected, normalized_code):
        return TotpVerificationResult(is_valid=True, matched_step=step)
```

### Timing Attacks — PASS

All secret comparisons use `hmac.compare_digest`:
- Bootstrap token (manual env-var path): `compare_digest(submitted_token.encode(), manual.encode())`
- Bootstrap token (DB hash path): `compare_digest(_hash_bootstrap_token(submitted_token), bootstrap_token_hash)`
- TOTP codes: `hmac.compare_digest(expected, normalized_code)`

No plain `==` comparisons on secrets anywhere in auth code.

### Brute-Force Protection — PASS

A DB-backed sliding-window rate limiter enforces lockout after 8 failed attempts within the window. The limiter is cross-replica-aware (uses the shared database, not in-process memory), so it applies correctly in multi-worker/multi-pod deployments. Tested by `test_cross_replica_combined_attempts_are_enforced`.

Rate limit budget is NOT spent before session validation — the session is validated first. This prevents a DoS vector where an attacker burns rate-limit budget using expired/invalid sessions.

### Request Log PII / Prompt Content — PASS

`to_request_log_entry` (`app/modules/request_logs/mappers.py:27`) stores only metadata: model, latency, token counts, cost, status, error codes. **No prompt text, no user message content.** Prompt cache keys are SHA-256 hashed before storage (`_hash_identifier` in `app/modules/proxy/service.py`).

### Upstream OAuth Token Storage — PASS

Upstream account OAuth tokens are stored Fernet-encrypted in the database (`app/modules/accounts/mappers.py:168`). The cleartext token is never persisted — only the encrypted bytes are written to the DB.

### SSRF — Image Inlining — PASS (with residual risk in L-3)

`_resolve_safe_image_fetch_target` enforces: HTTPS-only, no embedded URL credentials, configurable hostname allowlist, literal IP blocking for RFC-1918/loopback/link-local ranges, and DNS resolution pinning. See L-3 for the residual DNS TOCTOU note.

### OAuth CSRF / State Validation — PASS

OAuth authorization uses PKCE with S256 challenge (`generate_pkce_pair`, `pkce_challenge`). State parameter is included and validated on callback. No implicit flow, no `client_secret` exposed to the browser.

### Trusted-Header Spoofing — PASS (misconfiguration risk in L-1)

`DashboardAuthProxyHeaderSanitizerMiddleware` strips the configured dashboard proxy-auth header from all requests where the direct TCP peer is NOT in `firewall_trusted_proxy_cidrs`. This runs unconditionally before any auth logic.

### IP Forwarding / Firewall Bypass — PASS

`resolve_connection_client_ip` only trusts `X-Forwarded-For` and related headers when `firewall_trust_proxy_headers=true` AND the direct TCP peer is within `firewall_trusted_proxy_cidrs`. Falls back to raw socket IP otherwise, preventing header-based firewall bypass.

---

## 8. Dependency Analysis

| Package | Min version | Notes |
|---|---|---|
| `fastapi[standard]` | 0.128.0 | Current; standard includes starlette, uvicorn |
| `cryptography` | 46.0.3 | Recent release; Fernet + hazmat layer |
| `bcrypt` | 4.3.0 | Current; no known CVEs |
| `pyotp` | 2.9.0 | Standard RFC 6238 TOTP |
| `aiohttp` | 3.13.3 | Has historical CVE activity (CRLF injection, header smuggling in older versions) — current version clean; keep pinned and updated |
| `aiohttp-retry` | 2.9.1 | Small retry wrapper |
| `httpx` | 0.28.1 | Used in tests; current |
| `sqlalchemy` | 2.0.45 | Modern async ORM; parameterized queries throughout |
| `pydantic` | 2.12.5 | v2; strong validation; no known critical CVEs |
| `pydantic-settings` | 2.12.0 | Current |
| `opentelemetry-instrumentation-*` | 0.46 | Observability only; no security surface |

**No JWT libraries are used.** Sessions use Fernet encryption directly, avoiding JWT-specific attack classes (algorithm confusion, `none` algorithm, weak secret brute-force).

**Recommendation:** Add `pip-audit` or `safety` to CI/CD to automatically catch upstream CVEs in dependencies.

---

## 9. Architecture Decision Records Referenced

The following ADRs were created as part of this review and persisted to the codebase-memory-mcp knowledge graph:

| ADR | Decision |
|---|---|
| ADR-001 | Layered middleware security model |
| ADR-002 | Stateless Fernet-encrypted session tokens |
| ADR-003 | Bootstrap token for initial admin setup |
| ADR-004 | API key hashing (SHA-256) |
| ADR-005 | Dashboard password hashing (bcrypt) |
| ADR-006 | SSRF mitigations for image inlining |
| ADR-007 | Trusted-header dashboard auth mode |
| ADR-008 | IP resolution and firewall |
| ADR-009 | OAuth PKCE flow |
| ADR-010 | Request log sanitization |

---

## 10. Recommendations Summary

### Prioritized action list

| Priority | Finding | Effort | Impact |
|---|---|---|---|
| 1 | M-2: Add session revocation on password/TOTP change | Medium | High — closes active-session-after-credential-change gap |
| 2 | M-3: Add Fernet key rotation via MultiFernet | Medium | High — enables incident response and key hygiene |
| 3 | M-1: Salt API key hashes (PBKDF2 + per-key salt) | Low | Medium — defense-in-depth after DB compromise |
| 4 | L-1: Warn on overly broad trusted-proxy CIDR | Low | Medium — prevents silent misconfiguration |
| 5 | L-2: Change samesite to strict | Trivial | Low — incremental hardening |
| 6 | L-4: Atomic key file write | Low | Low — fixes theoretical startup race |
| 7 | Add pip-audit to CI | Trivial | High (ongoing) — catches supply-chain CVEs |
| 8 | M-4: Bootstrap token consistency | Low | Low — hygiene only |

### Not recommended

- Switching proxy API key validation to bcrypt without the hot-path cache layer — would add 100ms+ latency to every uncached API request.
- Adding server-side session state (Redis/DB) without carefully considering the multi-replica consistency model — the current stateless design is a feature, not a bug; the generation-counter approach (M-2 recommendation) preserves statelessness while enabling revocation.

---

*Review complete. No critical or high-severity vulnerabilities found. The codebase demonstrates strong security fundamentals: correct use of timing-safe comparisons throughout, proper bcrypt for passwords, SSRF defenses, header sanitization, IP-gated proxy auth, and no prompt content in logs.*
