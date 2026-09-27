"""Codex ChatGPT-backend passthrough over real HTTP.

The app runs under uvicorn on loopback and a real aiohttp server stands in for
upstream ChatGPT (or for the account's HTTP forward proxy), so streaming,
disconnect, and egress behavior are observed on the wire rather than through
httpx's buffering ASGI transport.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
import pytest
import uvicorn
from aiohttp import web

import app.core.clients.codex as codex_module
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountProxyBinding, AccountStatus, ProxyEndpoint, ProxyPool, ProxyPoolMember
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

_POOL_TOKEN = "pool-stored-access-token"
_CALLER_TOKEN = "caller-own-chatgpt-token"
_REJECTED_TOKEN = "rejected-by-upstream"

# Non-MCP backend paths Codex 0.157.0 sent with chatgpt_base_url = ".../backend-api"
# (captured 2026-09-27; see the change's context.md).
_CODEX_0157_BACKEND_PATHS = (
    "wham/accounts/check",
    "wham/settings/user",
    "ps/plugins/installed?includeExtensions=true&limit=200",
    "ps/plugins/list?scope=GLOBAL&limit=200",
    "ps/plugins/suggested/codex?scope=GLOBAL",
    "plugins/featured?platform=codex",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class _SeenRequest:
    method: str
    host: str
    path_qs: str
    headers: dict[str, str]
    body: bytes


@dataclass
class _FakeChatGPT:
    """Real aiohttp server answering ``/backend-api/*`` like upstream ChatGPT."""

    seen: list[_SeenRequest] = field(default_factory=list)
    release_second_event: asyncio.Event = field(default_factory=asyncio.Event)
    hold_disconnected: asyncio.Event = field(default_factory=asyncio.Event)
    port: int = 0
    _runner: web.AppRunner | None = None

    def forwarded(self) -> list[_SeenRequest]:
        """Requests other than codex-lb's own usage-identity checks."""

        return [seen for seen in self.seen if seen.path_qs != "/backend-api/wham/usage"]

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        self.seen.append(
            _SeenRequest(
                method=request.method,
                host=request.host,
                path_qs=request.rel_url.path_qs,
                headers={key.lower(): value for key, value in request.headers.items()},
                body=await request.read(),
            )
        )
        path = request.rel_url.path
        if path == "/backend-api/wham/usage":
            return web.json_response({"plan_type": "plus"})
        if path == "/backend-api/wham/settings/user" and request.headers.get("Authorization") == (
            f"Bearer {_REJECTED_TOKEN}"
        ):
            return web.json_response({"detail": "token rejected"}, status=401)
        if path == "/backend-api/ps/mcp":
            response = web.StreamResponse(
                headers={"Content-Type": "text/event-stream", "Mcp-Session-Id": "s1", "Set-Cookie": "edge=1"}
            )
            await response.prepare(request)
            await response.write(b"event: message\ndata: first\n\n")
            await asyncio.wait_for(self.release_second_event.wait(), timeout=10)
            await response.write(b"event: message\ndata: second\n\n")
            await response.write_eof()
            return response
        if path == "/backend-api/wham/hold":
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b"data: held\n\n")
            try:
                await asyncio.sleep(3600)
            finally:
                self.hold_disconnected.set()
            return response
        return web.json_response(
            {"path": path},
            headers={"Set-Cookie": "edge=1", "X-Edge-Internal": "1", "Request-Id": "up-1"},
        )

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle)
        # Cancel handlers when codex-lb drops the connection, so ``/hold`` can observe it.
        self._runner = web.AppRunner(app, shutdown_timeout=0.1, handler_cancellation=True)
        await self._runner.setup()
        self.port = _free_port()
        await web.TCPSite(self._runner, "127.0.0.1", self.port).start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


@pytest.fixture
async def fake_chatgpt() -> AsyncIterator[_FakeChatGPT]:
    upstream = _FakeChatGPT()
    await upstream.start()
    try:
        yield upstream
    finally:
        await upstream.stop()


@pytest.fixture
async def live_base_url(app_instance, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    """Serve the real app on loopback, lifespan included."""

    monkeypatch.setattr(codex_module, "discover_native_egress_client", lambda: None)
    port = _free_port()
    async with app_instance.router.lifespan_context(app_instance):
        server = uvicorn.Server(uvicorn.Config(app_instance, host="127.0.0.1", port=port, lifespan="off"))
        task = asyncio.create_task(server.serve())
        try:
            while not server.started:
                await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            await task


@pytest.fixture
def direct_upstream(fake_chatgpt: _FakeChatGPT, monkeypatch: pytest.MonkeyPatch) -> _FakeChatGPT:
    monkeypatch.setattr(get_settings(), "upstream_base_url", f"http://127.0.0.1:{fake_chatgpt.port}/backend-api")
    return fake_chatgpt


async def _seed_account(
    account_id: str,
    chatgpt_account_id: str,
    *,
    status: AccountStatus = AccountStatus.ACTIVE,
    proxy_port: int | None = None,
    empty_proxy_pool: bool = False,
) -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                chatgpt_account_id=chatgpt_account_id,
                email=f"{account_id}@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt(_POOL_TOKEN),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                id_token_encrypted=encryptor.encrypt("id"),
                last_refresh=utcnow(),
                status=status,
                deactivation_reason=None,
            )
        )
        if proxy_port is not None or empty_proxy_pool:
            pool = ProxyPool(id=f"pool-{account_id}", name=f"pool {account_id}")
            session.add(pool)
            if proxy_port is not None:
                endpoint = ProxyEndpoint(
                    id=f"ep-{account_id}", name="fake proxy", scheme="http", host="127.0.0.1", port=proxy_port
                )
                session.add(endpoint)
                session.add(ProxyPoolMember(pool_id=pool.id, endpoint_id=endpoint.id))
            session.add(AccountProxyBinding(account_id=account_id, pool_id=pool.id))
        await session.commit()


def _caller_headers(chatgpt_account_id: str, token: str = _CALLER_TOKEN) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "chatgpt-account-id": chatgpt_account_id,
        "User-Agent": "codex_cli_rs/0.157.0",
        "originator": "codex_cli_rs",
    }


# --- 3.1 routing -----------------------------------------------------------


@pytest.mark.asyncio
async def test_unserved_paths_reach_upstream_unchanged(live_base_url: str, direct_upstream: _FakeChatGPT) -> None:
    await _seed_account("acc-route", "cgpt-route")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        check = await client.get("/backend-api/wham/accounts/check", headers=_caller_headers("cgpt-route"))
        plugins = await client.get(
            "/backend-api/ps/plugins/list",
            params={"scope": "GLOBAL", "limit": "200"},
            headers=_caller_headers("cgpt-route"),
        )

    assert check.status_code == 200
    assert check.json() == {"path": "/backend-api/wham/accounts/check"}
    assert plugins.status_code == 200
    assert [seen.path_qs for seen in direct_upstream.forwarded()] == [
        "/backend-api/wham/accounts/check",
        "/backend-api/ps/plugins/list?scope=GLOBAL&limit=200",
    ]


@pytest.mark.asyncio
async def test_wham_usage_is_served_locally_and_matches_api_codex_usage(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    await _seed_account("acc-usage", "cgpt-usage")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        wham = await client.get("/backend-api/wham/usage", headers=_caller_headers("cgpt-usage"))
        api = await client.get("/api/codex/usage", headers=_caller_headers("cgpt-usage"))

    assert wham.status_code == 200
    assert wham.json() == api.json()
    # Only codex-lb's own identity/refresh fetches reached upstream /wham/usage;
    # the caller's request itself was answered locally, not relayed.
    assert direct_upstream.forwarded() == []


@pytest.mark.asyncio
async def test_wham_consume_is_served_by_the_reset_credit_handler(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    await _seed_account("acc-served", "cgpt-served")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.post(
            "/backend-api/wham/rate-limit-reset-credits/consume",
            json={"redeem_request_id": ""},
            headers=_caller_headers("cgpt-served"),
        )

    # The dedicated handler rejects the empty id itself; nothing is relayed.
    assert response.status_code == 400
    assert direct_upstream.forwarded() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/backend-api/codex/not-a-served-route",
        "/backend-api/files/file_abc",
        "/backend-api/transcribe/extra",
        "/backend-api/a/%2e%2e/%2e%2e/secret",
        "/backend-api/x%3Fy",
        "/backend-api/x%5Cy",
        "/api/codex/accounts/check",
    ],
)
async def test_closed_and_escaping_paths_are_not_forwarded(
    live_base_url: str, direct_upstream: _FakeChatGPT, path: str
) -> None:
    await _seed_account("acc-closed", "cgpt-closed")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get(path, headers=_caller_headers("cgpt-closed"))

    assert response.status_code == 404
    assert direct_upstream.seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/backend-api/", "/backend-api/does-not-exist"])
async def test_anonymous_unknown_paths_keep_the_root_not_found_envelope(
    live_base_url: str, direct_upstream: _FakeChatGPT, path: str
) -> None:
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get(path)

    assert response.status_code == 404
    assert response.json() == {"error": {"message": "Not Found", "type": "invalid_request_error", "code": "not_found"}}
    assert direct_upstream.seen == []


# --- 3.2 identity ----------------------------------------------------------


@pytest.mark.asyncio
async def test_caller_credentials_are_forwarded_not_pool_credentials(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    await _seed_account("acc-ident", "cgpt-ident")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get("/backend-api/wham/settings/user", headers=_caller_headers("cgpt-ident"))

    assert response.status_code == 200
    (seen,) = direct_upstream.seen
    assert seen.headers["authorization"] == f"Bearer {_CALLER_TOKEN}"
    assert seen.headers["chatgpt-account-id"] == "cgpt-ident"
    assert _POOL_TOKEN not in str(seen.headers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "seed_status"),
    [
        ({"Authorization": f"Bearer {_CALLER_TOKEN}", "chatgpt-account-id": "cgpt-unknown"}, None),
        ({"Authorization": f"Bearer {_CALLER_TOKEN}", "chatgpt-account-id": "cgpt-paused"}, AccountStatus.PAUSED),
        ({"Authorization": f"Bearer {_CALLER_TOKEN}"}, None),
    ],
    ids=["unknown-account", "inactive-account", "missing-account-id"],
)
async def test_unauthenticated_callers_get_401_without_upstream_call(
    live_base_url: str,
    direct_upstream: _FakeChatGPT,
    headers: dict[str, str],
    seed_status: AccountStatus | None,
) -> None:
    if seed_status is not None:
        await _seed_account("acc-paused", "cgpt-paused", status=seed_status)
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.post("/backend-api/ps/mcp", content=b"{}", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"
    assert direct_upstream.seen == []


@pytest.mark.asyncio
async def test_uncredentialed_mcp_client_falls_through_like_an_unknown_path(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    # Codex's MCP client sends no credentials to a non-chatgpt.com host.
    mcp_headers = {"User-Agent": "codex-mcp-client/0.157.0", "originator": "codex_exec"}
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        mcp = await client.post("/backend-api/ps/mcp", content=b"{}", headers=mcp_headers)
        unknown = await client.post("/backend-api/codex/does-not-exist", content=b"{}", headers=mcp_headers)

    assert mcp.status_code == unknown.status_code == 405
    assert mcp.json() == unknown.json()
    assert direct_upstream.seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer sk-clb-not-a-chatgpt-identity"},
        {
            "Authorization": f"Bearer {_CALLER_TOKEN}",
            "chatgpt-account-id": "cgpt-cap",
            "X-Codex-LB-Required-Capability": "anything",
        },
    ],
    ids=["api-key-principal", "capability-carrier"],
)
async def test_proxy_api_key_principals_get_404_without_upstream_call(
    live_base_url: str, direct_upstream: _FakeChatGPT, headers: dict[str, str]
) -> None:
    await _seed_account("acc-cap", "cgpt-cap")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get("/backend-api/wham/settings/user", headers=headers)

    assert response.status_code == 404
    assert direct_upstream.seen == []


@pytest.mark.asyncio
async def test_upstream_token_rejection_is_relayed_and_account_stays_healthy(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    await _seed_account("acc-reject", "cgpt-reject")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get(
            "/backend-api/wham/settings/user", headers=_caller_headers("cgpt-reject", token=_REJECTED_TOKEN)
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "token rejected"}
    async with SessionLocal() as session:
        account = await session.get(Account, "acc-reject")
    assert account is not None
    assert account.status == AccountStatus.ACTIVE
    assert account.deactivation_reason is None


# --- 3.3 streaming and header hygiene --------------------------------------


@pytest.mark.asyncio
async def test_event_stream_is_streamed_before_upstream_finishes(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    await _seed_account("acc-mcp", "cgpt-mcp")
    headers = {**_caller_headers("cgpt-mcp"), "Mcp-Session-Id": "s1", "Cookie": "dashboard_session=secret"}
    async with httpx.AsyncClient(base_url=live_base_url, timeout=10) as client:
        async with client.stream("POST", "/backend-api/ps/mcp", content=b'{"jsonrpc":"2.0"}', headers=headers) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert resp.headers["mcp-session-id"] == "s1"
            assert "set-cookie" not in resp.headers
            chunks = resp.aiter_bytes()
            first = await asyncio.wait_for(chunks.__anext__(), timeout=5)
            # Upstream is still blocked on the second event here.
            assert b"data: first" in first
            assert not direct_upstream.release_second_event.is_set()
            direct_upstream.release_second_event.set()
            rest = b"".join([chunk async for chunk in chunks])

    assert b"data: second" in rest
    (seen,) = direct_upstream.seen
    assert seen.body == b'{"jsonrpc":"2.0"}'
    assert seen.headers["mcp-session-id"] == "s1"
    assert "cookie" not in seen.headers


@pytest.mark.asyncio
async def test_response_headers_are_allowlisted(live_base_url: str, direct_upstream: _FakeChatGPT) -> None:
    await _seed_account("acc-hdr", "cgpt-hdr")
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get("/backend-api/wham/accounts/check", headers=_caller_headers("cgpt-hdr"))

    assert response.headers["request-id"] == "up-1"
    assert "set-cookie" not in response.headers
    assert "x-edge-internal" not in response.headers
    assert direct_upstream.seen


@pytest.mark.asyncio
async def test_client_disconnect_closes_upstream_stream(live_base_url: str, direct_upstream: _FakeChatGPT) -> None:
    await _seed_account("acc-hold", "cgpt-hold")
    async with httpx.AsyncClient(base_url=live_base_url, timeout=10) as client:
        async with client.stream("GET", "/backend-api/wham/hold", headers=_caller_headers("cgpt-hold")) as resp:
            first = await asyncio.wait_for(resp.aiter_bytes().__anext__(), timeout=5)
            assert b"held" in first

    await asyncio.wait_for(direct_upstream.hold_disconnected.wait(), timeout=10)


# --- 3.4 egress ------------------------------------------------------------


@pytest.mark.asyncio
async def test_bound_account_forwards_through_its_proxy(
    live_base_url: str, fake_chatgpt: _FakeChatGPT, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fake server plays the account's HTTP forward proxy; the upstream host
    # itself is unresolvable, so the request can only arrive via the proxy.
    monkeypatch.setattr(get_settings(), "upstream_base_url", "http://upstream.invalid/backend-api")
    await _seed_account("acc-proxied", "cgpt-proxied", proxy_port=fake_chatgpt.port)
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get("/backend-api/wham/accounts/check", headers=_caller_headers("cgpt-proxied"))

    assert response.status_code == 200
    (seen,) = fake_chatgpt.seen
    assert seen.host == "upstream.invalid"
    assert seen.path_qs == "/backend-api/wham/accounts/check"


@pytest.mark.asyncio
async def test_unresolvable_route_fails_closed(live_base_url: str, direct_upstream: _FakeChatGPT) -> None:
    await _seed_account("acc-noroute", "cgpt-noroute", empty_proxy_pool=True)
    async with httpx.AsyncClient(base_url=live_base_url) as client:
        response = await client.get("/backend-api/wham/accounts/check", headers=_caller_headers("cgpt-noroute"))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_error"
    assert direct_upstream.seen == []


# --- 3.5 path drift --------------------------------------------------------


@pytest.mark.asyncio
async def test_codex_0157_backend_paths_are_forwarded_unchanged(
    live_base_url: str, direct_upstream: _FakeChatGPT
) -> None:
    await _seed_account("acc-drift", "cgpt-drift")
    async with httpx.AsyncClient(base_url=live_base_url, timeout=10) as client:
        for path in _CODEX_0157_BACKEND_PATHS:
            response = await client.get(f"/backend-api/{path}", headers=_caller_headers("cgpt-drift"))
            assert response.status_code == 200, path

    assert [seen.path_qs for seen in direct_upstream.seen] == [
        f"/backend-api/{path}" for path in _CODEX_0157_BACKEND_PATHS
    ]
