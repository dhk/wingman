"""Warm-path lookups via Woven (#81, RFC-043): on-demand, never persisted.

Wingman tracks people and companies for a job search but has no visibility
into who in the user's actual network could make an introduction. This
module is the thin bridge: given a wingman-tracked person or company name,
it asks Woven's own graph — live, on demand — and returns Woven's answer
verbatim. Identity resolution is entirely Woven's: its fuzzy matching
(aliases, nicknames, typo tolerance) is the only name resolution used;
wingman never tries to map its own records onto Woven's graph nodes, and
never caches or stores what comes back.
"""

from __future__ import annotations

from wingman.application.ingest import IngestError
from wingman.infrastructure.woven_client import (
    WovenCallError,
    WovenCaller,
    WovenNotConfigured,
    call_woven_tool,
)


def _run(tool: str, arguments: dict[str, object], caller: WovenCaller | None = None) -> str:
    call = caller if caller is not None else call_woven_tool
    try:
        return call(tool, arguments)
    except WovenNotConfigured as exc:
        raise IngestError(str(exc)) from exc
    except WovenCallError as exc:
        raise IngestError(f"Woven lookup failed: {exc}") from exc


def warm_paths_to_person(
    name: str, from_person: str = "", caller: WovenCaller | None = None
) -> str:
    """Woven's warmest path to a person, by name — Woven's own fuzzy match,
    not a wingman-side lookup. from_person narrows the search to paths
    starting from a specific network owner, when Woven's graph pools more
    than one."""
    if not name.strip():
        raise IngestError("a person name is required.")
    arguments: dict[str, object] = {"to": name.strip()}
    if from_person.strip():
        arguments["from"] = from_person.strip()
    return _run("find_warmest_paths", arguments, caller)


def warm_overview_for_company(
    company: str, from_person: str = "", caller: WovenCaller | None = None
) -> str:
    """Woven's full picture of a company: reachable people ranked by hops
    then warmth, coverage stats, and the best entry point."""
    if not company.strip():
        raise IngestError("a company name is required.")
    arguments: dict[str, object] = {"company": company.strip()}
    if from_person.strip():
        arguments["from"] = from_person.strip()
    return _run("company_overview", arguments, caller)
