"""One sanitized line per HTTP request on the shared service (logger
'wingman.access').

It exists to answer "did the client reach us, and what did we answer?"
from journald, which nothing else could: uvicorn's own access log stays
disabled because capability tokens live in URL paths (#70), and the OAuth
bearer path only logs after a valid token arrives — so a connector failing
at discovery left no trace at all.

What is logged: method, a path normalised against an allowlist, status,
duration, and the user-agent's leading product token. What is never logged:
query strings (OAuth codes and state), any other header (Authorization,
cookies), bodies, and any path segment that could be a credential or a
private file name. The product token is client-controlled; it is narrowed to
a short, restricted-alphabet value rather than trusted.

The path is an allowlist, not a redaction: anything not recognised is
logged as '<other>'. A mistyped capability URL is still a capability
token, and a redaction rule only protects the shapes its author thought of.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from wingman.infrastructure.logs import get_logger

_logger = get_logger("access")

_SAFE_ROUTES = frozenset(
    {
        "/mcp",
        "/health",
        "/login",
        "/oauth/callback",
        "/setup",
        "/setup/",
        "/setup/keys",
        "/setup/upload",
        "/setup/logout",
    }
)
# Discovery documents a client may fetch or probe. Their suffix is logged only
# when it is the MCP route itself; anything else is collapsed, since a client
# can put any text there.
_WELL_KNOWN_NAMES = frozenset(
    {"oauth-protected-resource", "oauth-authorization-server", "openid-configuration"}
)
_CAPABILITY = re.compile(r"/(?P<kind>mcp|ui|admin)/[^/]+(?P<tail>/.*)?")
_UA_PRODUCT = re.compile(r"[A-Za-z0-9._+/-]{1,40}")


def _split_mount(path: str, mounts: frozenset[str]) -> tuple[str, str]:
    for mount in sorted(mounts, key=len, reverse=True):
        if path == mount or path.startswith(mount + "/"):
            return mount, path[len(mount) :]
    return "", path


def loggable_path(path: str, mounts: frozenset[str] = frozenset()) -> str:
    """The request path in a form that can never carry a credential.

    Only mount prefixes the server was configured with are logged as
    themselves. A capability token is 32 URL-safe characters, so any other
    leading segment could be one (a mistyped '/<token>/mcp'), and is not.
    """
    mount, rest = _split_mount(path, mounts)
    if rest in _SAFE_ROUTES:
        return mount + rest
    if rest.startswith("/.well-known/"):
        name, _, suffix = rest[len("/.well-known/") :].partition("/")
        if name not in _WELL_KNOWN_NAMES:
            return f"{mount}/.well-known/<other>"
        known_suffixes = {"", "mcp"} | {f"{m.strip('/')}/mcp" for m in mounts}
        if suffix in known_suffixes:
            return mount + rest
        return f"{mount}/.well-known/{name}/…"
    match = _CAPABILITY.fullmatch(rest)
    if match:
        tail = "/…" if match.group("tail") is not None else ""
        return f"{mount}/{match.group('kind')}/<token>{tail}"
    return "<other>"


def sanitize_user_agent(raw: bytes | str | None) -> str:
    """Only the leading product token, e.g. 'Claude-User/1.0'.

    The header is client-controlled. Keeping just the first product token
    (letters, digits and ._+/-, at most 40 characters) still tells clients
    apart while leaving almost no room for anything else.
    """
    if raw is None:
        return "-"
    text = raw.decode("latin-1") if isinstance(raw, bytes) else raw
    match = _UA_PRODUCT.match(text.strip())
    return match.group(0) if match else "-"


class AccessLogMiddleware:
    """Pure ASGI wrapper: logs once per HTTP request, after it finishes.

    Status is taken from 'http.response.start'. If the app raises before
    responding the line says 500 and the exception propagates unchanged;
    if the client went away first the status is '-'. Logging never raises.
    """

    def __init__(self, app: ASGIApp, mounts: Iterable[str] = ()) -> None:
        self.app = app
        self.mounts = frozenset(m.rstrip("/") for m in mounts if m.strip("/"))

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
        aborted = False
        try:
            await self.app(scope, receive, recording_send)
        except Exception:
            failed = True
            raise
        except BaseException:
            # Cancellation: the client went away or the server is stopping.
            # Not a 500, and not the success a sent status line would suggest.
            aborted = True
            raise
        finally:
            self._log(scope, status, failed, aborted, started)

    def _log(
        self, scope: Scope, status: list[int], failed: bool, aborted: bool, started: float
    ) -> None:
        try:
            if status:
                code = str(status[0])
            else:
                code = "500" if failed else "-"
            user_agent = next(
                (value for key, value in scope.get("headers", []) if key.lower() == b"user-agent"),
                None,
            )
            method = str(scope.get("method", "-"))
            _logger.info(
                'method=%s path=%s status=%s ms=%d ua="%s"%s',
                method if method.isalpha() and len(method) <= 16 else "<other>",
                loggable_path(str(scope.get("path", "")), self.mounts),
                code,
                int((time.monotonic() - started) * 1000),
                sanitize_user_agent(user_agent),
                " aborted=1" if aborted else "",
            )
        except Exception:  # noqa: BLE001, S110 — a log line must never break a request
            pass
