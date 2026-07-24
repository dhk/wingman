"""Woven MCP bridge (RFC-043, #81): on-demand warm-path lookups, nothing stored.

Woven (a separate project, `dhk/woven`) builds a warm-intro-path graph from
pooled LinkedIn exports and exposes it as its own MCP server. Wingman does
not embed or duplicate that graph — it calls out, live, per request, the
same "the invocation is the consent" posture as every other network read in
this codebase (RFC-009). Nothing from Woven's response is ever persisted
into the wingman workspace; every call is a fresh read, and Woven's own
fuzzy name/company matching is the only identity resolution used — wingman
never tries to map its own Person records onto Woven's graph nodes (Woven
has no persistent IDs, only normalizeId(name); a second, wingman-side
matcher would just be a worse copy).

Configured via WINGMAN_WOVEN_URL — a streamable-HTTP MCP endpoint, the same
transport wingman's own --http server offers (RFC-017). Absent config is
not an error: every caller degrades to one clear, actionable message rather
than a stack trace.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import TextContent

from wingman.infrastructure.logs import get_logger

_logger = get_logger("infrastructure.woven_client")

ENV_WOVEN_URL = "WINGMAN_WOVEN_URL"


class WovenNotConfigured(Exception):
    """No Woven endpoint configured — the feature is inert, not broken."""


class WovenCallError(Exception):
    """The configured Woven endpoint could not be reached, or the tool call failed."""


# (tool_name, arguments) -> the tool's text content, verbatim. The default
# implementation is call_woven_tool below; application code takes this as an
# injectable parameter so tests never need a live Woven server (the fetcher
# pattern used throughout this codebase — RFC-009 discipline).
WovenCaller = Callable[[str, dict[str, object]], str]


def woven_url(env: Mapping[str, str] | None = None) -> str | None:
    """The configured Woven MCP endpoint, or None when unset."""
    environment = os.environ if env is None else env
    url = environment.get(ENV_WOVEN_URL, "").strip()
    return url or None


async def _call(url: str, tool: str, arguments: dict[str, object]) -> str:
    async with streamablehttp_client(url) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
            parts = [block.text for block in result.content if isinstance(block, TextContent)]
            text = "\n".join(parts) if parts else "(no result)"
            if result.isError:
                raise WovenCallError(text)
            return text


def call_woven_tool(
    tool: str, arguments: dict[str, object], env: Mapping[str, str] | None = None
) -> str:
    """Call one Woven MCP tool by name; returns its text content verbatim.

    Raises WovenNotConfigured when WINGMAN_WOVEN_URL is unset, WovenCallError
    on any connection or tool-level failure. Never caches, never persists —
    every call is a fresh, on-demand read (RFC-043).

    Runs the async call in its own thread with its own event loop rather
    than a bare `asyncio.run()` on the calling thread: the MCP tool that
    fronts this (`woven_warm_path`) is a plain sync function, and FastMCP
    invokes sync tools inline on its own already-running event loop
    (`fn(**arguments)`, not offloaded to a worker thread) — a bare
    `asyncio.run()` there raises "cannot be called from a running event
    loop" on every real invocation. Spinning up a dedicated thread sidesteps
    that regardless of whether the caller (CLI or MCP dispatch) already has
    a loop running.
    """
    url = woven_url(env)
    if url is None:
        raise WovenNotConfigured(
            f"{ENV_WOVEN_URL} is not set — the Woven integration is inert. "
            "Set it to Woven's streamable-HTTP MCP endpoint to enable warm-path lookups."
        )
    _logger.info("woven call tool=%s", tool)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_call(url, tool, arguments))).result()
    except WovenCallError:
        raise
    except Exception as exc:  # noqa: BLE001 — one clear failure, never a raw traceback
        raise WovenCallError(f"could not reach Woven at {url}: {exc}") from exc
