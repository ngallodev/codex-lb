"""Streaming passthrough for Codex's ChatGPT-backend calls.

Codex sends these calls to ``chatgpt_base_url`` under the user's own ChatGPT
login. When that base URL is codex-lb's ``/backend-api``, the calls codex-lb
does not serve are forwarded here to the same upstream path, under the
caller's own token (never a pool account's), with the body streamed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from app.core.clients.codex import CodexClient, create_codex_session, release_codex_response
from app.core.clients.http import HttpClientLease, acquire_http_client
from app.core.clients.proxy import _build_upstream_headers
from app.core.config.dashboard_overrides import with_dashboard_overrides
from app.core.config.settings import get_settings
from app.core.upstream_proxy import ResolvedUpstreamRoute

_BODY_CHUNK_BYTES = 64 * 1024

# Headers the caller sends that must not reach upstream: cookies are the
# caller's browser state for codex-lb, and compressed bodies would have to be
# decoded per transport before relay, so upstream is asked for identity.
_DROPPED_REQUEST_HEADERS = frozenset({"cookie", "accept-encoding"})


@dataclass(slots=True)
class CodexBackendStream:
    """An open upstream response whose body has not been read yet."""

    status_code: int
    headers: Mapping[str, str]
    _response: Any
    _owned_session: aiohttp.ClientSession | None = None
    _lease: HttpClientLease | None = None
    _closed: bool = field(default=False, init=False)

    async def iter_body(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._response.content.iter_chunked(_BODY_CHUNK_BYTES):
                if chunk:
                    yield chunk
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        """Release the upstream connection; safe to call more than once."""

        if self._closed:
            return
        self._closed = True
        try:
            await release_codex_response(self._response)
        finally:
            if self._owned_session is not None:
                await self._owned_session.close()
            if self._lease is not None:
                await self._lease.close()


def backend_upstream_url(rest: str) -> str:
    """Map ``/backend-api/<rest>`` on codex-lb to the same path upstream."""

    settings = with_dashboard_overrides(get_settings())
    base = settings.upstream_base_url.rstrip("/")
    if "/backend-api" not in base:
        base = f"{base}/backend-api"
    return f"{base}/{rest}"


def _passthrough_request_headers(inbound: Mapping[str, str], access_token: str, account_id: str) -> dict[str, str]:
    kept = {key: value for key, value in inbound.items() if key.lower() not in _DROPPED_REQUEST_HEADERS}
    headers = _build_upstream_headers(kept, access_token, account_id, accept=inbound.get("accept", "*/*"))
    for key in [key for key in headers if key.lower() == "content-type"]:
        del headers[key]
    content_type = inbound.get("content-type")
    if content_type:
        headers["Content-Type"] = content_type
    headers["Accept-Encoding"] = "identity"
    return headers


async def open_codex_backend_stream(
    rest: str,
    *,
    method: str,
    body: bytes | None,
    query_params: Sequence[tuple[str, str]],
    inbound_headers: Mapping[str, str],
    access_token: str,
    chatgpt_account_id: str,
    route: ResolvedUpstreamRoute | None,
) -> CodexBackendStream:
    """Send one passthrough request and return its response before reading the body.

    ``route`` is the caller's account egress route; ``None`` means the account
    has no proxy binding and egress is direct, as for its usage-identity check.
    """

    settings = with_dashboard_overrides(get_settings())
    url = backend_upstream_url(rest)
    headers = _passthrough_request_headers(inbound_headers, access_token, chatgpt_account_id)
    timeout = aiohttp.ClientTimeout(
        total=None,
        sock_connect=settings.upstream_connect_timeout_seconds,
        sock_read=settings.stream_idle_timeout_seconds,
    )
    if route is not None:
        session = create_codex_session()
        try:
            response = await CodexClient(session).request(
                method,
                url,
                route=route,
                params=list(query_params),
                data=body,
                headers=headers,
                timeout=timeout,
                buffer_response=False,
            )
        except BaseException:
            await session.close()
            raise
        return CodexBackendStream(
            status_code=int(getattr(response, "status_code", getattr(response, "status", 0))),
            headers=dict(getattr(response, "headers", {}) or {}),
            _response=response,
            _owned_session=session,
        )

    lease = await acquire_http_client()
    try:
        response = await lease.client.session.request(
            method,
            url,
            params=list(query_params),
            data=body,
            headers=headers,
            timeout=timeout,
        )
    except BaseException:
        await lease.close()
        raise
    return CodexBackendStream(
        status_code=response.status,
        headers=dict(response.headers),
        _response=response,
        _lease=lease,
    )


__all__ = ["CodexBackendStream", "open_codex_backend_stream", "backend_upstream_url"]
