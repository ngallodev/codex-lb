"""Serve Codex's ChatGPT-backend calls when ``chatgpt_base_url`` is codex-lb.

See ``openspec/specs/codex-backend-passthrough``. With ``chatgpt_base_url``
set to codex-lb's ``/backend-api``, Codex sends every backend call on its
chatgpt.com path. codex-lb answers pooled usage itself (the ``/wham`` aliases
below) and forwards the rest upstream unchanged under the caller's identity.
This router is included after every other ``/backend-api`` router, so
first-match routing keeps served routes authoritative.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from starlette.datastructures import Headers
from starlette.routing import Match
from starlette.types import Receive, Scope, Send

from app.core.auth.dependencies import (
    CodexCallerIdentity,
    set_openai_error_format,
    validate_codex_backend_passthrough_identity,
)
from app.core.clients.codex import CodexTransportError
from app.core.clients.codex_backend import CodexBackendStream, open_codex_backend_stream
from app.core.clients.proxy import CODEX_LB_REQUIRED_CAPABILITY_HEADER
from app.core.errors import openai_error
from app.modules.proxy import api as proxy_api
from app.modules.proxy.schemas import ConsumeRateLimitResetCreditResponse, RateLimitStatusPayload

logger = logging.getLogger(__name__)

router = APIRouter(tags=["proxy"], dependencies=[Depends(set_openai_error_format)])

_PASSTHROUGH_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]

# Allowlist, not denylist: anything upstream adds (cookies, edge headers,
# encodings the body no longer has) stays behind. ``www-authenticate`` carries
# MCP auth discovery; ``mcp-session-id`` binds later MCP calls to this session.
_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-type",
        "etag",
        "last-modified",
        "location",
        "mcp-session-id",
        "openai-processing-ms",
        "request-id",
        "retry-after",
        "www-authenticate",
        "x-request-id",
    }
)

_FORBIDDEN_PATH_CHARACTERS = frozenset("?#\\")

# Pool-routed namespaces: an unserved path here must not bypass the pool under
# the caller's own account (a new model endpoint, or a pool-pinned file id).
_CLOSED_NAMESPACES = ("codex", "files", "transcribe")


class _UpstreamStreamingResponse(StreamingResponse):
    """Close the upstream response however the downstream side ends.

    The body generator releases upstream when it finishes or is cancelled, but
    it never runs at all if the response fails before the first chunk (or for
    HEAD), so the release is also owned here.
    """

    def __init__(self, stream: CodexBackendStream) -> None:
        super().__init__(
            stream.iter_body(),
            status_code=stream.status_code,
            headers={key: value for key, value in stream.headers.items() if key.lower() in _RESPONSE_HEADERS},
        )
        self._upstream = stream

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._upstream.aclose()


def _forwardable_rest(rest: str) -> bool:
    """Whether ``rest`` stays inside upstream ``/backend-api/`` and outside pool-routed namespaces."""

    segments = rest.split("/")
    return not (
        not rest
        or rest.startswith("/")
        or any(segment in {".", ".."} for segment in segments)
        or any(char in _FORBIDDEN_PATH_CHARACTERS or ord(char) < 0x20 for char in rest)
        or segments[0] in _CLOSED_NAMESPACES
    )


def _carries_chatgpt_identity(headers: Headers) -> bool:
    """Whether the caller presents a ChatGPT bearer rather than nothing or a codex-lb principal."""

    if headers.getlist(CODEX_LB_REQUIRED_CAPABILITY_HEADER):
        return False
    scheme, _, token = (headers.get("authorization") or "").strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return False
    return not (token.startswith("sk-clb-") and not headers.get("chatgpt-account-id"))


class _ForwardableBackendRoute(APIRoute):
    """Match only requests the passthrough may forward.

    Everything else falls through to codex-lb's usual unmatched-path handling,
    so unknown, pool-namespace, anonymous, and codex-lb-principal requests keep
    the exact 404/405 envelopes they had before this route existed.
    """

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        match, child_scope = super().matches(scope)
        if match is Match.NONE:
            return match, child_scope
        if not _forwardable_rest(child_scope["path_params"]["rest"]) or not _carries_chatgpt_identity(
            Headers(scope=scope)
        ):
            return Match.NONE, {}
        return match, child_scope


# Codex calls these on the ``/wham`` path in the ``/backend-api`` style; they
# reuse the ``/api/codex`` handlers so auth and payload cannot drift apart.
for _path in ("/backend-api/wham/usage", "/backend-api/wham/usage/"):
    router.add_api_route(
        _path,
        proxy_api.codex_usage,
        methods=["GET"],
        response_model=RateLimitStatusPayload,
        include_in_schema=_path.endswith("usage"),
    )
for _path in (
    "/backend-api/wham/rate-limit-reset-credits/consume",
    "/backend-api/wham/rate-limit-reset-credits/consume/",
):
    router.add_api_route(
        _path,
        proxy_api.codex_consume_rate_limit_reset_credit,
        methods=["POST"],
        response_model=ConsumeRateLimitResetCreditResponse,
        include_in_schema=_path.endswith("consume"),
    )


async def codex_backend_passthrough(
    request: Request,
    rest: str,
    identity: CodexCallerIdentity = Depends(validate_codex_backend_passthrough_identity),
) -> Response:
    upstream_rest = rest
    body = await request.body() if request.method not in {"GET", "HEAD"} else None
    try:
        stream = await open_codex_backend_stream(
            upstream_rest,
            method=request.method,
            body=body or None,
            query_params=request.query_params.multi_items(),
            inbound_headers=request.headers,
            access_token=identity.access_token,
            chatgpt_account_id=identity.chatgpt_account_id,
            route=identity.route,
        )
    except (CodexTransportError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning(
            "Codex backend passthrough upstream unavailable method=%s path=%s account_id=%s error=%s",
            request.method,
            upstream_rest,
            identity.account_id,
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=502,
            content=openai_error("upstream_unavailable", "Upstream ChatGPT backend is unavailable"),
        )
    logger.info(
        "Codex backend passthrough method=%s path=%s account_id=%s status=%s",
        request.method,
        upstream_rest,
        identity.account_id,
        stream.status_code,
    )
    return _UpstreamStreamingResponse(stream)


router.add_api_route(
    "/backend-api/{rest:path}",
    codex_backend_passthrough,
    methods=_PASSTHROUGH_METHODS,
    include_in_schema=False,
    response_model=None,
    route_class_override=_ForwardableBackendRoute,
)
