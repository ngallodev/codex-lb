## 1. Identity gate

- [x] 1.1 Split `validate_codex_usage_identity` in `app/core/auth/dependencies.py` into a local stage (bearer + `chatgpt-account-id` → active account, route resolution) and the existing upstream `/wham/usage` stage (which keeps the workspace remap, since it needs the upstream payload). Keep `/api/codex/usage` behavior identical; verify `tests/integration/test_codex_usage_api.py` and `tests/integration/test_native_usage_egress.py` still pass unchanged.
- [x] 1.2 Add a `validate_codex_backend_passthrough_identity` dependency that runs only the local stage (401 for a missing account id or an unknown/inactive account). Verify with the identity tests in 3.2.

## 2. Routes and forwarding

- [x] 2.1 Add a streaming upstream helper in `app/core/clients/codex_backend.py` that targets `<upstream base>/<rest>` unchanged, builds headers from the caller's token with `Cookie` dropped and `Accept-Encoding: identity`, and releases the upstream response and route lease on completion, error, or disconnect. Verify with 3.3 and 3.4.
- [x] 2.2 In `app/modules/proxy/codex_backend_passthrough.py`, register `/backend-api/wham/usage` and `/backend-api/wham/rate-limit-reset-credits/consume` (with and without a trailing slash) against the existing `/api/codex` handlers. Verify with 3.1 that both answer identically to their `/api/codex` twins.
- [x] 2.3 Register the `/backend-api/{rest:path}` catch-all for GET/HEAD/POST/PUT/PATCH/DELETE on the same router, included in `app/main.py` after `usage_router`. Its route class declines closed namespaces, escaping paths, requests without a ChatGPT bearer, `sk-clb-` principals, and capability carriers, so they fall through to the normal unmatched-path answer. Remove the earlier `/api/codex/{rest:path}` catch-all, and add the `wham` aliases to the capability-route inventory. Verify with 3.1 and with `test_health_and_errors.py`, `test_proxy_images.py`, `test_proxy_compact.py`, and `test_daybreak_capability_routes.py`.

## 3. Integration tests (real app under uvicorn, real aiohttp fake upstream; no mocks of internal components)

- [x] 3.1 Routing: `wham/accounts/check` and `ps/plugins/list?scope=GLOBAL&limit=200` reach upstream unchanged; `/backend-api/wham/usage` is served locally and matches `/api/codex/usage`; `codex/…`, `files/…`, and `transcribe/…` unserved paths and `..` traversal return 404 with no upstream call; `/api/codex/<unserved>` is no longer forwarded.
- [x] 3.2 Identity: the caller's token and account id arrive upstream and the pool account's stored token does not; unknown/inactive account and missing account id return 401 with no upstream call; an uncredentialed MCP call, an `sk-clb-` bearer, and a capability carrier fall through to the unserved-path answer; an upstream 401 is relayed and the pool account stays healthy.
- [x] 3.3 Streaming: an SSE response streams its first event before upstream finishes; `Mcp-Session-Id` round-trips; `Cookie`/`Set-Cookie` are stripped; response headers are allowlisted; a client disconnect mid-stream closes the upstream connection.
- [x] 3.4 Egress: an account bound to an upstream proxy pool forwards through that route; an unresolvable route returns 503 `upstream_error` with no direct-egress request.
- [x] 3.5 Path-drift fixture: every non-MCP path captured from Codex 0.157.0 in the `/backend-api` style (see context.md) is forwarded to the identical upstream path.

## 5. Operator log seams (added 2026-09-27 after deploy review)

- [x] 5.1 Log one stream-end record per forwarded call (method, path, account, status, outcome, duration_ms, bytes; WARNING on `error` or 5xx), a DEBUG record with the reason when the route declines a request, and a DEBUG identity record with egress. Verify with the log-seam tests in `tests/integration/test_codex_backend_passthrough.py` (completed, upstream 5xx, mid-stream failure, client disconnect, four decline reasons, proxy-pool egress).

## 6. Pre-review follow-ups (CodeRabbit on the fork PR)

- [x] 6.1 Classify aiohttp client errors from the upstream body as `outcome=error` before the caller-disconnect branch, including when Starlette (ASGI 2.4) re-raises the `OSError` as `ClientDisconnect` (checked via the exception context). Verify with `tests/unit/test_codex_backend_passthrough_outcome.py`: the `ClientOSError` cases fail on the previous code under both ASGI 2.3 and 2.4 and pass now; the payload-error and caller-side cases pass on both.

## 4. Docs and validation

- [x] 4.1 Update the "Showing pooled quota in Codex" section in `docs/client-setup.md` to the `/backend-api` setting and state that connectors are unavailable with it; link to `openspec/specs/codex-backend-passthrough/`.
- [x] 4.2 Run `npx --yes @fission-ai/openspec@1.11.0 validate forward-codex-chatgpt-backend-calls --strict`, `make lint`, and `uv run ty check`; all pass.
- [x] 4.3 Live check against the running instance: with `chatgpt_base_url` pointed at codex-lb's `/backend-api`, the server log shows the captured Codex paths forwarded with upstream 200s (MCP excepted), and the Codex TUI `/status` shows the pooled percentage.
