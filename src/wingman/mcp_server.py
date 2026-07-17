"""Wingman MCP server: the workspace as tools for a local MCP client (RFC-008).

A stdio server for Claude Desktop / Claude Code on the same machine. The
workspace never leaves the machine; every tool runs the same deterministic
validation pipelines as the CLI, so the connected model can request work but
cannot bypass evidence rules. No external actions exist — nothing to approve.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from wingman.agents.profile_curator import ProposalParseError
from wingman.application.assess import assess_job as assess_job_use_case
from wingman.application.corpus import find_evidence
from wingman.application.ingest import IngestError, ingest_resume
from wingman.infrastructure.config import Config, load_config
from wingman.infrastructure.logs import configure_logging
from wingman.infrastructure.storage import CorpusSearchError, Storage
from wingman.providers.base import CapabilityClass, ProviderError
from wingman.providers.router import ModelConfigError, get_provider

server = FastMCP("wingman")

_NOT_INITIALIZED = (
    "The Wingman workspace is not initialized on this machine. "
    "Run 'wingman init' in a terminal first."
)


def _ready_config() -> Config | None:
    config = load_config()
    return config if config.db_path.exists() else None


@server.tool()
def status() -> str:
    """Workspace status: counts of source records, profile items, opportunities, and corpus documents."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    with Storage(config.db_path) as storage:
        return (
            f"Workspace: {config.data_dir}\n"
            f"Source records: {storage.count_source_records()}\n"
            f"Profile items: {storage.count_profile_items()}\n"
            f"Opportunities: {storage.count_opportunities()}\n"
            f"Corpus documents: {storage.count_corpus_documents()}"
        )


@server.tool()
def evidence(query: str, limit: int = 10) -> str:
    """Search the user's own writing (corpus) for cited evidence.

    Full-text query: plain words, quoted phrases, or AND/OR/NOT. Returns
    ranked excerpts with their source documents; quotes are verbatim.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    try:
        with Storage(config.db_path) as storage:
            hits = find_evidence(query, storage, limit=limit)
    except CorpusSearchError as exc:
        return f"Search failed: {exc}"
    if not hits:
        return f"No corpus evidence found for {query!r}."
    lines = []
    for number, hit in enumerate(hits, start=1):
        when = (
            hit.document.published_at.date().isoformat() if hit.document.published_at else "undated"
        )
        lines.append(
            f"{number}. {hit.document.title} [{hit.document.source_type}, {when}]\n"
            f"   {hit.snippet}\n"
            f"   source: {hit.source_locator}"
        )
    return "\n".join(lines)


@server.tool()
def career_profile() -> str:
    """Return the current cited career profile (career.md), including roles, achievements, skills, and testimonials with their evidence."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    career_md = config.reports_dir / "career.md"
    if not career_md.exists():
        return (
            "No career profile has been generated yet. Ingest a resume "
            "('wingman ingest') or a LinkedIn export ('wingman ingest-linkedin') first."
        )
    return career_md.read_text(encoding="utf-8")


@server.tool()
def assess_job(job_description: str) -> str:
    """Assess a job description against the career profile; returns the cited fit brief.

    Requirements are extracted with verbatim quotes and each is judged
    met/partial/gap/unknown citing only real profile items — unsupported
    verdicts are downgraded by deterministic validation.
    """
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    if not job_description.strip():
        return "The job description is empty; nothing was assessed."
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    job_path = config.inbox_dir / f"{stamp}-pasted-job.md"
    job_path.write_text(job_description, encoding="utf-8")
    try:
        with Storage(config.db_path) as storage:
            report = assess_job_use_case(
                job_path,
                config,
                storage,
                get_provider(CapabilityClass.EXTRACT_FAST, config),
                get_provider(CapabilityClass.SYNTHESIZE_BALANCED, config),
            )
    except (IngestError, ModelConfigError, ProviderError, ProposalParseError) as exc:
        return f"Assessment failed: {exc}"
    return Path(report.brief_md_path).read_text(encoding="utf-8")


@server.tool()
def ingest_resume_text(resume_markdown: str, filename: str = "resume.md") -> str:
    """Ingest resume text into the canonical profile (model extraction plus deterministic evidence validation). Returns the ingestion summary."""
    config = _ready_config()
    if config is None:
        return _NOT_INITIALIZED
    if not resume_markdown.strip():
        return "The resume text is empty; nothing was ingested."
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    resume_path = config.inbox_dir / f"{stamp}-{filename}"
    resume_path.write_text(resume_markdown, encoding="utf-8")
    try:
        with Storage(config.db_path) as storage:
            report = ingest_resume(
                resume_path,
                config,
                storage,
                get_provider(CapabilityClass.EXTRACT_FAST, config),
            )
    except (IngestError, ModelConfigError, ProviderError, ProposalParseError) as exc:
        return f"Ingestion failed: {exc}"
    rejected = "".join(f"\n  rejected {item.name!r}: {item.reason}" for item in report.rejected)
    return (
        f"Accepted: {report.accepted}  Duplicates skipped: {report.skipped_duplicates}  "
        f"Evidence merged: {report.evidence_merged}  Conflicts: {report.conflicts}  "
        f"Rejected: {len(report.rejected)}{rejected}\n"
        f"Profile written to {report.career_md_path}"
    )


def main() -> None:
    configure_logging()
    server.run()


if __name__ == "__main__":
    main()
