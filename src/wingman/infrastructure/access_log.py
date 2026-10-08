"""One sanitized line per HTTP request on the shared service (logger
'wingman.access').

It exists to answer "did the client reach us, and what did we answer?"
from journald, which nothing else could: uvicorn's own access log stays
disabled because capability tokens live in URL paths (#70), and the OAuth
bearer path only logs after a valid token arrives — so a connector failing
at discovery left no trace at all.

What is logged: method, a path normalised against an allowlist, status,
duration, and a truncated user-agent. What is never logged: query strings
(OAuth codes and state), headers (Authorization, cookies), bodies, and any
path segment that could be a credential or a private file name.

The path is an allowlist, not a redaction: anything not recognised is
logged as '<other>'. A mistyped capability URL is still a capability
token, and a redaction rule only protects the shapes its author thought of.
"""

from __future__ import annotations

import re
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from wingman.infrastructure.logs import get_logger

_logger = get_logger("access")

# An optional single mount segment, e.g. '/shared' (a front such as a
# Tailscale funnel may or may not strip it before we see the request).
_MOUNT = r"(?P<mount>/[a-z0-9-]{1,32})?"
_SAFE = re.compile(
    _MOUNT
    + r"(?P<rest>/mcp|/health|/login|/oauth/callback|/setup|/setup/|/setup/keys"
    + r"|/setup/upload|/setup/logout)"
)
_WELL_KNOWN = re.compile(
    _MOUNT + r"(?P<rest>/\.well-known/[a-z0-9._-]{1,64}(?:/[a-z0-9._-]{1,64}){0,4})"
)
_CAPABILITY = re.compile(_MOUNT + r"/(?P<kind>mcp|ui|admin)/[^/]+(?P<tail>/.*)?")
_UA_LIMIT = 60
_UA_DROP = re.compile(r'["\\\x00-\x1f\x7f]')


def loggable_path(path: str) -> str:
    """The request path in a form that can never carry a credential."""
    for pattern in (_SAFE, _WELL_KNOWN):
        match = pattern.fullmatch(path)
        if match:
            return path
    match = _CAPABILITY.fullmatch(path)
    if match:
        suffix = "/…" if match.group("tail") is not None else ""
        return f"{match.group('mount') or ''}/{match.group('kind')}/<token>{suffix}"
    return "<other>"


def sanitize_user_agent(raw: bytes | str | None) -> str:
    if raw is None:
        return "-"
    text = raw.decode("latin-1") if isinstance(raw, bytes) else raw
    text = _UA_DROP.sub("", text)
    if len(text) > _UA_LIMIT:
        return text[:_UA_LIMIT] + "…"
    return text or "-"


class AccessLogMiddleware:
    """Pure ASGI wrapper: logs once per HTTP request, after it finishes.

    Status is taken from 'http.response.start'. If the app raises before
    responding the line says 500 and the exception propagates unchanged;
    if the client went away first the status is '-'. Logging never raises.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status: list[int] = []

        async def recording_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status.append(int(message["status"]))
            await send(message)

        failed = False
        try:
            await self.app(scope, receive, recording_send)
        except BaseException:
            failed = True
            raise
        finally:
            self._log(scope, status, failed, started)

    @staticmethod
    def _log(scope: Scope, status: list[int], failed: bool, started: float) -> None:
        try:
            if status:
                code = str(status[0])
            else:
                code = "500" if failed else "-"
            user_agent = next(
                (value for key, value in scope.get("headers", []) if key.lower() == b"user-agent"),
                None,
            )
            _logger.info(
                'method=%s path=%s status=%s ms=%d ua="%s"',
                _UA_DROP.sub("", str(scope.get("method", "-")))[:16],
                loggable_path(str(scope.get("path", ""))),
                code,
                int((time.monotonic() - started) * 1000),
                sanitize_user_agent(user_agent),
            )
        except Exception:  # noqa: BLE001, S110 — a log line must never break a request
            pass
