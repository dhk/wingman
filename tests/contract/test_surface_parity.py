"""Every live CLI leaf and MCP tool needs an inspectable parity decision."""

import asyncio
import re
import sys
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


def _check_ledger(text: str | None = None) -> None:
    assert LEDGER.is_file(), "Missing CLI/MCP parity decision ledger"
    rows: dict[tuple[str, str], tuple[list[str], str]] = {}
    for number, line in enumerate(
        (text if text is not None else LEDGER.read_text()).splitlines(), 1
    ):
        if not line.startswith(("| cli |", "| mcp |")):
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        assert len(cells) == 4, f"Malformed parity row at line {number}: expected four cells"
        surface, name, counterparts, reason = cells
        assert re.fullmatch(r"`[^`]+`", name), f"Malformed parity row at line {number}: name"
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
        if not counterparts:
            assert not re.match(r"(?i)^(todo|tbd|unknown|n/a|pending)\b", reason.strip()), (
                f"Placeholder reason: {surface} {name}"
            )
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


@pytest.mark.parametrize("reason", ["TODO", "TBD", "unknown", "n/a", "pending"])
def test_placeholder_is_not_a_parity_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    text = LEDGER.read_text()
    line = next(line for line in text.splitlines() if line.startswith("| cli | `ingest` |"))
    replacement = "| cli | `ingest` |  | " + reason + " |"
    local = tmp_path / "ledger.md"
    local.write_text(text.replace(line, replacement))
    monkeypatch.setattr(sys.modules[__name__], "LEDGER", local)
    with pytest.raises(AssertionError, match="Placeholder reason"):
        _check_ledger()


def test_malformed_row_has_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "ledger.md"
    local.write_text(LEDGER.read_text() + "\n| cli | `broken` |\n")
    monkeypatch.setattr(sys.modules[__name__], "LEDGER", local)
    with pytest.raises(AssertionError, match="Malformed parity row"):
        _check_ledger()


def test_reviewed_counterparts_match_existing_operations() -> None:
    text = LEDGER.read_text()
    assert "| cli | `today` | `changelog` |" in text
    assert "| mcp | `changelog` | `today` |" in text
    assert "| cli | `people add-feed` | `feed_attach`, `feed_discover` |" in text
    assert "| mcp | `motd` | `motd history` |" in text
    for command in ("motd set", "motd show", "qotd set"):
        assert f"| cli | `{command}` |  | Deliberately local operator" in text


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing-cli", "cli parity drift"),
        ("missing-mcp", "mcp parity drift"),
        ("stale-cli", "cli parity drift"),
        ("stale-mcp", "mcp parity drift"),
        ("duplicate", "Duplicate parity decision"),
        ("blank-reason", "No counterpart or reason"),
        ("unknown-counterpart", "Unknown counterpart"),
        ("nonreciprocal", "Nonreciprocal mapping"),
    ],
)
def test_ledger_mutations_are_rejected(mutation: str, message: str) -> None:
    text = LEDGER.read_text()
    cli = next(line for line in text.splitlines() if line.startswith("| cli | `status` |"))
    mcp = next(line for line in text.splitlines() if line.startswith("| mcp | `status` |"))
    ingest = next(line for line in text.splitlines() if line.startswith("| cli | `ingest` |"))
    if mutation.startswith("missing-"):
        text = text.replace(cli if mutation.endswith("cli") else mcp, "")
    elif mutation.startswith("stale-"):
        text += f"\n| {mutation.split('-')[1]} | `removed-entry` |  | Deliberately removed. |\n"
    elif mutation == "duplicate":
        text += "\n" + cli
    elif mutation == "blank-reason":
        text = text.replace(ingest, "| cli | `ingest` |  |  |")
    elif mutation == "unknown-counterpart":
        text = text.replace(cli, "| cli | `status` | `missing_tool` |  |")
    else:
        text = text.replace(mcp, "| mcp | `status` |  | Deliberately unmatched. |")
    with pytest.raises(AssertionError, match=message):
        _check_ledger(text)
