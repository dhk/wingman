"""Warm-path lookups via Woven (#81, RFC-043): on-demand bridge, nothing stored."""

import pytest

from wingman.application.ingest import IngestError
from wingman.application.warm_intro import warm_overview_for_company, warm_paths_to_person
from wingman.infrastructure.woven_client import (
    ENV_WOVEN_URL,
    WovenCallError,
    WovenNotConfigured,
    call_woven_tool,
    woven_url,
)


def _fake_caller(text: str = "Jane Author -> Brian Chen -> Target Person (warmth 0.8)"):
    calls: list[tuple[str, dict[str, object]]] = []

    def caller(tool: str, arguments: dict[str, object]) -> str:
        calls.append((tool, arguments))
        return text

    caller.calls = calls  # type: ignore[attr-defined]
    return caller


def test_warm_paths_to_person_calls_find_warmest_paths_with_target_name() -> None:
    caller = _fake_caller()
    result = warm_paths_to_person("Target Person", caller=caller)
    assert "Target Person" in result or "warmth" in result
    tool, arguments = caller.calls[0]  # type: ignore[attr-defined]
    assert tool == "find_warmest_paths"
    assert arguments == {"to": "Target Person"}


def test_warm_paths_to_person_passes_from_person_when_given() -> None:
    caller = _fake_caller()
    warm_paths_to_person("Target Person", from_person="Dave Holmes", caller=caller)
    tool, arguments = caller.calls[0]  # type: ignore[attr-defined]
    assert arguments == {"to": "Target Person", "from": "Dave Holmes"}


def test_warm_paths_to_person_requires_a_name() -> None:
    with pytest.raises(IngestError, match="required"):
        warm_paths_to_person("   ", caller=_fake_caller())


def test_warm_overview_for_company_calls_company_overview() -> None:
    caller = _fake_caller("Acme: 4 reachable, best entry Jane Author (warmth 0.9)")
    result = warm_overview_for_company("Acme", caller=caller)
    assert "Acme" in result
    tool, arguments = caller.calls[0]  # type: ignore[attr-defined]
    assert tool == "company_overview"
    assert arguments == {"company": "Acme"}


def test_warm_overview_requires_a_company() -> None:
    with pytest.raises(IngestError, match="required"):
        warm_overview_for_company("", caller=_fake_caller())


def test_not_configured_surfaces_as_ingest_error() -> None:
    def caller(tool: str, arguments: dict[str, object]) -> str:
        raise WovenNotConfigured(f"{ENV_WOVEN_URL} is not set")

    with pytest.raises(IngestError, match="WINGMAN_WOVEN_URL"):
        warm_paths_to_person("Target Person", caller=caller)


def test_call_error_surfaces_as_ingest_error() -> None:
    def caller(tool: str, arguments: dict[str, object]) -> str:
        raise WovenCallError("connection refused")

    with pytest.raises(IngestError, match="Woven lookup failed"):
        warm_overview_for_company("Acme", caller=caller)


def test_woven_url_reads_env_var_and_degrades_to_none() -> None:
    assert woven_url({}) is None
    assert woven_url({ENV_WOVEN_URL: "  "}) is None
    assert woven_url({ENV_WOVEN_URL: "https://woven.example/mcp"}) == "https://woven.example/mcp"


def test_call_woven_tool_raises_not_configured_when_env_unset() -> None:
    with pytest.raises(WovenNotConfigured, match="WINGMAN_WOVEN_URL"):
        call_woven_tool("find_warmest_paths", {"to": "X"}, env={})


def test_mcp_woven_warm_path_tool_requires_exactly_one_of_person_or_company() -> None:
    from wingman.mcp_server import woven_warm_path

    assert "exactly one" in woven_warm_path()
    assert "exactly one" in woven_warm_path(person="X", company="Y")


def test_mcp_woven_warm_path_tool_reports_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from wingman.mcp_server import woven_warm_path

    monkeypatch.delenv(ENV_WOVEN_URL, raising=False)
    result = woven_warm_path(person="Target Person")
    assert "failed" in result and "WINGMAN_WOVEN_URL" in result
    # protocol/behavior notes ride the docstring
    assert "from Woven" in (woven_warm_path.__doc__ or "")
