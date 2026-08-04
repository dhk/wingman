"""Telemetry (RFC-023): opt-in, local-only, never breaks the command it records."""

import asyncio
import json
from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.telemetry_harvest import harvest_transcript
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.infrastructure.telemetry import (
    count_events,
    is_enabled,
    iter_events,
    list_events,
    record_event,
    redact_argv,
    set_enabled,
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "ws"
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(data_dir))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    Storage(config.db_path).close()
    return data_dir


def test_default_off_and_recording_gated(workspace: Path) -> None:
    config = load_config()
    assert not is_enabled(config)
    assert not record_event(config, "cli", "status", {"argv": ["status"]})
    assert count_events(config) == 0
    set_enabled(config, True)
    assert is_enabled(config)
    assert record_event(config, "cli", "status", {"argv": ["status"]}, duration_ms=12)
    assert count_events(config) == 1
    set_enabled(config, False)
    assert not record_event(config, "cli", "status", {"argv": ["status"]})
    assert count_events(config) == 1  # off stops recording, keeps history


def test_events_roundtrip_and_clipping(workspace: Path) -> None:
    config = load_config()
    set_enabled(config, True)
    record_event(config, "mcp", "search", {"args": {"query": "x"}, "result": "y" * 10_000})
    [event] = list_events(config)
    assert event["surface"] == "mcp" and event["name"] == "search"
    assert len(event["payload"]["result"]) <= 4_000  # clipped, not duplicated
    assert list(iter_events(config))[0]["name"] == "search"


def test_keys_set_values_are_never_journaled() -> None:
    assert redact_argv(["keys", "set", "anthropic", "--value", "sk-secret"]) == [
        "keys",
        "set",
        "anthropic",
        "[value withheld]",
    ]
    assert redact_argv(["keys", "list"]) == ["keys", "list"]
    assert redact_argv(["search", "sk-looking-string"]) == ["search", "sk-looking-string"]


def test_mcp_tool_calls_are_journaled(workspace: Path) -> None:
    from wingman.mcp_server import server

    config = load_config()
    set_enabled(config, True)
    result = asyncio.run(server.call_tool("status", {}))
    assert result  # the tool still works, wrapped
    events = [event for event in list_events(config) if event["surface"] == "mcp"]
    assert events and events[0]["name"] == "status"
    assert "Workspace" in events[0]["payload"]["result"]
    assert events[0]["outcome"] == "ok"


def test_mcp_telemetry_summary_action(workspace: Path) -> None:
    from wingman.mcp_server import server

    config = load_config()
    set_enabled(config, True)
    record_event(config, "cli", "search", {}, ts="2026-08-01T09:00:00+00:00")
    record_event(config, "cli", "status", {}, ts="2026-08-01T11:00:00+00:00")

    result = asyncio.run(
        server.call_tool("telemetry", {"action": "summary", "gap_minutes": 30, "top": 5})
    )
    text = str(result)
    assert "Sessions: 2" in text
    assert "search" in text


def test_harvest_transcript(workspace: Path, tmp_path: Path) -> None:
    config = load_config()
    lines = [
        {
            "type": "user",
            "timestamp": "2026-07-18T20:00:00Z",
            "message": {"role": "user", "content": "build the search utility"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-07-18T20:00:05Z",
            "message": {
                "content": [
                    {"type": "text", "text": "Building it now."},
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "uv run wingman search 'semantic'"},
                    },
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
                    {
                        "type": "tool_use",
                        "name": "mcp__wingman__people_pov",
                        "input": {"name": "Jane"},
                    },
                ]
            },
        },
        {"not": "a transcript line"},
        "garbage that is not json",
    ]
    transcript = tmp_path / "session.jsonl"
    with transcript.open("w", encoding="utf-8") as handle:
        for line in lines:
            handle.write((json.dumps(line) if isinstance(line, dict) else line) + "\n")

    with pytest.raises(IngestError, match="telemetry on"):
        harvest_transcript(transcript, config)  # harvesting requires the opt-in
    set_enabled(config, True)
    report = harvest_transcript(transcript, config)
    assert report.by_kind == {
        "user-message": 1,
        "assistant-message": 1,
        "wingman-cli-invocation": 1,
        "wingman-mcp-call": 1,
    }
    assert report.imported == 4 and report.skipped_lines == 1
    events = {event["name"]: event for event in list_events(config)}
    assert events["user-message"]["payload"]["text"] == "build the search utility"
    assert events["user-message"]["ts"] == "2026-07-18T20:00:00Z"  # original time kept
    assert "wingman search" in events["wingman-cli-invocation"]["payload"]["command"]
    assert events["wingman-mcp-call"]["payload"]["tool"] == "mcp__wingman__people_pov"
    with pytest.raises(IngestError, match="does not exist"):
        harvest_transcript(tmp_path / "missing.jsonl", config)
