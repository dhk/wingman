"""Wingman command-line interface."""

from typing import Annotated

import typer

app = typer.Typer(help="Wingman: local-first career intelligence.")


@app.command()
def init(
    data_dir: Annotated[str, typer.Option(help="Local data directory.")] = "./data",
) -> None:
    """Initialize a local Wingman workspace."""
    typer.echo(f"Wingman workspace ready at {data_dir}")


@app.command()
def doctor() -> None:
    """Check the local Wingman environment."""
    typer.echo("Wingman environment check: OK")


@app.command()
def status() -> None:
    """Show the current Wingman workspace status."""
    typer.echo("Wingman status: Phase 0 scaffold")


if __name__ == "__main__":
    app()
