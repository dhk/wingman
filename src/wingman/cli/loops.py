"""CLI parity for the three loop tools; output is the same structured card."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from wingman.application.loop_tools import run_loop_tool

loop_app = typer.Typer(help="Version-pinned, resumable interview loops.")


def _print(result: str) -> None:
    typer.echo(result)
    if json.loads(result).get("status") == "error":
        raise typer.Exit(1)


@loop_app.command("list")
def catalogue() -> None:
    """List promises, durations, sensitivity and tiers."""
    _print(run_loop_tool("list"))


@loop_app.command("start")
def start(loop_id: str = "goals-friction") -> None:
    """Start or resume at the same card."""
    _print(run_loop_tool("start", loop_id=loop_id))


@loop_app.command("answer")
def answer_card(
    session_id: str,
    answer: str = "",
    action: str = "answer",
    choice: Annotated[list[str] | None, typer.Option("--choice")] = None,
    confirmation: str = "",
    persona_authored: bool = False,
    owner: str = "user",
) -> None:
    """Preview an answer, then --action confirm --confirmation TOKEN after review."""
    _print(
        run_loop_tool(
            "answer",
            session_id=session_id,
            answer=answer,
            action=action,
            choices=choice,
            confirmation=confirmation,
            persona_authored=persona_authored,
            owner=owner,
        )
    )
