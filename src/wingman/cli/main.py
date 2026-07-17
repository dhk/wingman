"""Wingman command-line interface."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import typer

from wingman.agents.profile_curator import ProposalParseError
from wingman.application.assess import assess_job
from wingman.application.ingest import IngestError, ingest_resume
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.logs import configure_logging
from wingman.infrastructure.storage import Storage
from wingman.providers.base import CapabilityClass, ProviderError
from wingman.providers.router import DEFAULT_MODELS_TOML, ModelConfigError, get_provider

app = typer.Typer(help="Wingman: local-first career intelligence.")

MIN_PYTHON = (3, 12)


def _workspace_dirs(config: Config) -> list[Path]:
    return [config.data_dir, config.inbox_dir, config.reports_dir]


@app.command()
def init() -> None:
    """Initialize the local Wingman workspace (idempotent).

    The workspace lives in $WINGMAN_DATA_DIR if set, otherwise the platform
    user data directory.
    """
    configure_logging()
    config = load_config()
    created: list[Path] = []
    for directory in _workspace_dirs(config):
        if not directory.exists():
            try:
                directory.mkdir(parents=True)
            except OSError as exc:
                typer.echo(
                    f"init failed: could not create {directory} ({exc}). "
                    f"Directories already created were left in place: {created or 'none'}. "
                    f"Fix permissions or set {ENV_DATA_DIR} to a writable path, then re-run "
                    "'wingman init'.",
                    err=True,
                )
                raise typer.Exit(code=1) from exc
            created.append(directory)
    try:
        Storage(config.db_path).close()
    except sqlite3.Error as exc:
        typer.echo(
            f"init failed: could not initialize the database at {config.db_path} ({exc}). "
            "The workspace directories were preserved. Check that the disk is writable, "
            "then re-run 'wingman init'.",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    if not config.models_config_path.exists():
        config.models_config_path.write_text(DEFAULT_MODELS_TOML, encoding="utf-8")
    typer.echo(f"Workspace ready at {config.data_dir} (from {config.data_dir_source}).")


@app.command()
def doctor() -> None:
    """Check the local Wingman environment and report each result."""
    configure_logging()
    config = load_config()
    failures = 0

    def report(name: str, ok: bool, detail: str) -> None:
        nonlocal failures
        if not ok:
            failures += 1
        typer.echo(f"[{'ok' if ok else 'FAIL'}] {name}: {detail}")

    python_ok = sys.version_info[:2] >= MIN_PYTHON
    report(
        "python",
        python_ok,
        f"{sys.version_info.major}.{sys.version_info.minor} "
        f"(requires >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]})",
    )
    report("config", True, f"data dir {config.data_dir} resolved from {config.data_dir_source}")
    if config.data_dir.exists():
        try:
            probe = config.data_dir / ".doctor-write-probe"
            probe.touch()
            probe.unlink()
            report("data dir", True, f"{config.data_dir} is writable")
        except OSError as exc:
            report("data dir", False, f"{config.data_dir} is not writable ({exc})")
    else:
        report("data dir", False, f"{config.data_dir} does not exist; run 'wingman init'")
    if config.db_path.exists():
        try:
            with Storage(config.db_path) as storage:
                storage.count_source_records()
            report("database", True, f"{config.db_path} is reachable")
        except sqlite3.Error as exc:
            report("database", False, f"{config.db_path} could not be opened ({exc})")
    else:
        report("database", False, f"{config.db_path} does not exist; run 'wingman init'")

    if failures:
        typer.echo(f"{failures} check(s) failed.", err=True)
        raise typer.Exit(code=1)
    typer.echo("All checks passed.")


@app.command()
def status() -> None:
    """Show the current Wingman workspace status."""
    configure_logging()
    config = load_config()
    typer.echo(f"Workspace: {config.data_dir} (from {config.data_dir_source})")
    if not config.db_path.exists():
        typer.echo("Database: not initialized — run 'wingman init'.")
        return
    with Storage(config.db_path) as storage:
        sources = storage.count_source_records()
        items = storage.count_profile_items()
        opportunities = storage.count_opportunities()
    typer.echo(f"Database: {config.db_path}")
    typer.echo(f"Source records: {sources}")
    typer.echo(f"Profile items: {items}")
    typer.echo(f"Opportunities: {opportunities}")


@app.command()
def ingest(
    resume: Path = typer.Argument(..., help="Path to a resume in Markdown or plain text."),
) -> None:
    """Ingest a resume into the canonical profile and write career.json / career.md."""
    configure_logging()
    config = load_config()
    if not config.db_path.exists():
        typer.echo(
            f"Workspace at {config.data_dir} is not initialized. Nothing was ingested; "
            "run 'wingman init' first.",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        provider = get_provider(CapabilityClass.EXTRACT_FAST, config)
        with Storage(config.db_path) as storage:
            report = ingest_resume(resume, config, storage, provider)
    except (IngestError, ModelConfigError, ProviderError) as exc:
        typer.echo(f"ingest failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ProposalParseError as exc:
        typer.echo(
            f"ingest failed: {exc}. The source record was preserved; the profile was not "
            "changed. Re-run 'wingman ingest' to retry the extraction.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"Source record: {report.source_record_id}" + (" (reused)" if report.source_reused else "")
    )
    typer.echo(
        f"Accepted: {report.accepted}  Duplicates skipped: {report.skipped_duplicates}  "
        f"Evidence merged: {report.evidence_merged}  Conflicts: {report.conflicts}  "
        f"Rejected: {len(report.rejected)}"
    )
    for rejected in report.rejected:
        typer.echo(f"  rejected {rejected.name!r}: {rejected.reason}")
    typer.echo(f"Model: {report.provider}/{report.model} (prompt {report.prompt_version})")
    typer.echo(f"Wrote {report.career_json_path}")
    typer.echo(f"Wrote {report.career_md_path}")


@app.command()
def assess(
    job: Path = typer.Argument(..., help="Path to a job description in Markdown or plain text."),
) -> None:
    """Assess a job description against the profile and write a cited fit brief."""
    configure_logging()
    config = load_config()
    if not config.db_path.exists():
        typer.echo(
            f"Workspace at {config.data_dir} is not initialized. Nothing was assessed; "
            "run 'wingman init' first.",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        extract_provider = get_provider(CapabilityClass.EXTRACT_FAST, config)
        assess_provider = get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config)
        with Storage(config.db_path) as storage:
            report = assess_job(job, config, storage, extract_provider, assess_provider)
    except (IngestError, ModelConfigError, ProviderError) as exc:
        typer.echo(f"assess failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ProposalParseError as exc:
        typer.echo(
            f"assess failed: {exc}. The source record was preserved; no opportunity was "
            "created or changed. Re-run 'wingman assess' to retry.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    typer.echo(f"Opportunity: {report.title} ({report.opportunity_id})")
    verdicts = "  ".join(f"{name}: {count}" for name, count in sorted(report.verdicts.items()))
    typer.echo(f"Requirements: {report.requirements}  {verdicts}")
    for rejected in report.rejected_requirements:
        typer.echo(f"  rejected requirement {rejected.name!r}: {rejected.reason}")
    for note in report.downgraded:
        typer.echo(f"  validation: {note}")
    typer.echo(f"Next action: {report.next_action}")
    typer.echo(f"Wrote {report.brief_json_path}")
    typer.echo(f"Wrote {report.brief_md_path}")


if __name__ == "__main__":
    app()
