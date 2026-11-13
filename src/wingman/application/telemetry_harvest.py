"""Harvest a Claude Code session transcript into the telemetry journal (RFC-023).

Claude Code writes each session as JSONL under ~/.claude/projects/<project>/.
This parser walks one such file and imports, as telemetry events with their
original timestamps: every user message and assistant reply (the
conversation text — an explicit owner opt-in), every Bash invocation that
mentions wingman, and every wingman MCP tool call with its input. Lines
that don't parse or don't match are counted and skipped, never fatal —
transcripts are an external format we read defensively. If loop activity is
found anywhere, the entire transcript is imported as content-free event
metadata only; original conversation files are never modified.
"""

from __future__ import annotations

import json
import re
from itertools import islice
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from wingman.application.ingest import IngestError
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.telemetry import is_enabled, is_loop_operation, record_event

_logger = get_logger("application.telemetry_harvest")


class HarvestReport(BaseModel):
    source: str
    imported: int
    by_kind: dict[str, int] = Field(default_factory=dict)
    skipped_lines: int = 0
    content_withheld: bool = False


def _texts(content: Any) -> list[str]:
    """Text blocks of a message's content, whichever shape the transcript used."""
    if isinstance(content, str):
        return [content] if content.strip() else []
    collected: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text", ""))
                if text.strip():
                    collected.append(text)
    return collected


def _tool_uses(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    return [item for item in content if isinstance(item, dict) and item.get("type") == "tool_use"]


def _mentions_loop(line: str) -> bool:
    # Conservative: even discussion of the loop tool is enough to withhold
    # transcript content. We cannot reliably separate private interview turns.
    if re.search(r"loop_(?:start|answer|list)", line):
        return True
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(obj, dict) or not isinstance(obj.get("message"), dict):
        return False
    content = obj["message"].get("content")
    if any(is_loop_operation(command=text) for text in _texts(content)):
        return True
    for tool in _tool_uses(content):
        inputs = tool.get("input", {})
        command = str(inputs.get("command", "")) if isinstance(inputs, dict) else ""
        if is_loop_operation(tool=str(tool.get("name", "")), command=command):
            return True
    return False


def harvest_transcript(path: Path, config: Config) -> HarvestReport:
    """Import one Claude Code transcript; returns what was harvested."""
    path = path.expanduser()
    if not path.exists():
        raise IngestError(f"transcript {path} does not exist. Nothing was harvested.")
    if not is_enabled(config):
        raise IngestError(
            "telemetry is off — enable it first with 'wingman telemetry on' "
            "(harvesting writes into the telemetry journal)."
        )
    source = path.name
    report = HarvestReport(source=str(path), imported=0)

    def emit(kind: str, payload: dict[str, Any], ts: str | None) -> None:
        if report.content_withheld:
            payload = {"content": "[interview-loop transcript content withheld]"}
        payload["source"] = source
        if record_event(config, "transcript", kind, payload, ts=ts):
            report.imported += 1
            report.by_kind[kind] = report.by_kind.get(kind, 0) + 1

    with path.open(encoding="utf-8") as handle:
        # Inspect before the first write: an answer may precede its tool call.
        # Bound the import to the scanned lines if Claude appends while reading.
        line_count = 0
        for line in handle:
            line_count += 1
            if _mentions_loop(line):
                report.content_withheld = True
        handle.seek(0)
        for line in islice(handle, line_count):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                report.skipped_lines += 1
                continue
            if not isinstance(obj, dict):
                report.skipped_lines += 1
                continue
            ts = obj.get("timestamp")
            message = obj.get("message")
            if not isinstance(message, dict):
                continue
            kind = obj.get("type")
            content = message.get("content")
            if kind == "user":
                for text in _texts(content):
                    emit("user-message", {"text": text}, ts)
            elif kind == "assistant":
                for text in _texts(content):
                    emit("assistant-message", {"text": text}, ts)
                for tool in _tool_uses(content):
                    name = str(tool.get("name", ""))
                    tool_input = tool.get("input", {})
                    command = (
                        str(tool_input.get("command", "")) if isinstance(tool_input, dict) else ""
                    )
                    if name == "Bash" and "wingman" in command:
                        emit("wingman-cli-invocation", {"command": command}, ts)
                    elif "wingman" in name.lower():
                        emit(
                            "wingman-mcp-call",
                            {"tool": name, "input": tool_input},
                            ts,
                        )
    _logger.info(
        "harvest source=%s imported=%d skipped=%d", source, report.imported, report.skipped_lines
    )
    return report
