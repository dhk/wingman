"""Every live CLI leaf and MCP tool needs an inspectable parity decision."""

import asyncio
import re
from pathlib import Path

import pytest
import typer

from wingman.cli.main import app
from wingman.mcp_server import server

LEDGER = Path(__file__).resolve().parents[2] / "docs" / "SURFACE-PARITY.md"


def _cli_names(group: object, prefix: str = "") -> set[str]:
    result: set[str] = set()
    for name, command in group.commands.items():  # type: ignore[attr-defined]
        path = f"{prefix} {name}".strip()
        if hasattr(command, "commands"):
            result.update(_cli_names(command, path))
        else:
            result.add(path)
    return result


def _check_ledger() -> None:
    assert LEDGER.is_file(), "Missing CLI/MCP parity decision ledger"
    rows: dict[tuple[str, str], tuple[list[str], str]] = {}
    for line in LEDGER.read_text().splitlines():
        if not line.startswith(("| cli |", "| mcp |")):
            continue
        surface, name, counterparts, reason = (cell.strip() for cell in line.split("|")[1:-1])
        key = surface, name.strip("`")
        assert key not in rows, f"Duplicate parity decision: {key}"
        rows[key] = re.findall(r"`([^`]+)`", counterparts), reason
    actual = {
        "cli": _cli_names(typer.main.get_command(app)),
        "mcp": {tool.name for tool in asyncio.run(server.list_tools())},
    }
    for surface, names in actual.items():
        recorded = {name for kind, name in rows if kind == surface}
        assert names == recorded, (
            f"{surface} parity drift: undecided={sorted(names - recorded)}, "
            f"stale={sorted(recorded - names)}"
        )
    for (surface, name), (counterparts, reason) in rows.items():
        assert counterparts or reason.strip(), f"No counterpart or reason: {surface} {name}"
        other = "mcp" if surface == "cli" else "cli"
        for counterpart in counterparts:
            assert counterpart in actual[other], f"Unknown counterpart: {counterpart}"
            assert name in rows[other, counterpart][0], f"Nonreciprocal mapping: {name}"


def test_live_surfaces_have_parity_decisions() -> None:
    _check_ledger()


def test_new_cli_command_requires_a_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "registered_commands", list(app.registered_commands))

    @app.command("parity-undecided-command")
    def undecided() -> None:
        pass

    with pytest.raises(AssertionError, match="cli parity drift.*parity-undecided-command"):
        _check_ledger()


def test_new_mcp_tool_requires_a_decision() -> None:
    @server.tool(name="parity_undecided_tool")
    def undecided() -> str:
        return "test"

    try:
        with pytest.raises(AssertionError, match="mcp parity drift.*parity_undecided_tool"):
            _check_ledger()
    finally:
        server.remove_tool("parity_undecided_tool")
