"""Resume ingestion: the Phase 1 slice.

Deterministic ingestion and validation bracket the one model step:
read/hash/persist SourceRecord -> model extraction -> evidence validation ->
conflict detection -> persist -> render career.json / career.md.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from wingman.agents.profile_curator import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_prompt,
    parse_proposal,
)
from wingman.application.profile_store import persist_items
from wingman.domain import SourceRecord
from wingman.domain.extraction import ProposedItem
from wingman.domain.profile import EvidenceSpan, ProfileItem
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelProvider, ModelRequest
from wingman.reporting.career import render_career

_logger = get_logger("application.ingest")


class IngestError(Exception):
    """Ingestion failed; the message says what was preserved and how to recover."""


class RejectedItem(BaseModel):
    name: str
    reason: str


class IngestReport(BaseModel):
    source_record_id: str
    source_reused: bool
    accepted: int
    skipped_duplicates: int
    evidence_merged: int
    conflicts: int
    rejected: list[RejectedItem]
    career_json_path: Path
    career_md_path: Path
    provider: str
    model: str
    prompt_version: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None


def _read_resume(resume_path: Path) -> str:
    try:
        text = resume_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IngestError(
            f"could not read {resume_path} ({exc}). Nothing was ingested; "
            "check the path and re-run 'wingman ingest'."
        ) from exc
    except UnicodeDecodeError as exc:
        raise IngestError(
            f"{resume_path} is not UTF-8 text ({exc}). Nothing was ingested; "
            "convert the resume to Markdown or plain text and re-run 'wingman ingest'."
        ) from exc
    if not text.strip():
        raise IngestError(f"{resume_path} is empty. Nothing was ingested.")
    return text


def _persist_source(
    resume_path: Path, text: str, config: Config, storage: Storage
) -> tuple[SourceRecord, bool]:
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing = storage.get_source_record_by_hash(content_hash)
    if existing is not None:
        return existing, True
    resolved = resume_path.resolve()
    if resolved.is_relative_to(config.inbox_dir.resolve()):
        stored = resolved
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        stored = config.inbox_dir / f"{stamp}-{content_hash[:8]}-{resume_path.name}"
        shutil.copy2(resolved, stored)
    record = SourceRecord(
        source_type="resume",
        source_locator=str(stored.relative_to(config.data_dir.resolve()))
        if stored.is_relative_to(config.data_dir.resolve())
        else str(stored),
        content_hash=content_hash,
    )
    storage.add_source_record(record)
    return record, False


def _validate_evidence(
    proposed: ProposedItem, source_text: str, record: SourceRecord
) -> ProfileItem | RejectedItem:
    """Deterministic check: every quote must be non-blank and appear verbatim in the source."""
    for quote in proposed.quotes:
        if not quote.strip():
            return RejectedItem(name=proposed.name, reason="empty evidence quote")
        if quote not in source_text:
            return RejectedItem(
                name=proposed.name,
                reason=f"evidence quote not found verbatim in source: {quote[:80]!r}",
            )
    return ProfileItem(
        kind=proposed.kind,
        name=proposed.name,
        detail=proposed.detail,
        classification=proposed.classification,
        confidence=proposed.confidence,
        evidence=[
            EvidenceSpan(source_record_id=record.record_id, quote=quote)
            for quote in proposed.quotes
        ],
        prompt_version=PROMPT_VERSION,
        extracted_by="",
    )


def ingest_resume(
    resume_path: Path, config: Config, storage: Storage, provider: ModelProvider
) -> IngestReport:
    text = _read_resume(resume_path)
    record, reused = _persist_source(resume_path, text, config, storage)

    response = provider.complete(ModelRequest(system=SYSTEM_PROMPT, prompt=build_prompt(text)))
    proposal = parse_proposal(response.text)

    validated: list[ProfileItem] = []
    rejected: list[RejectedItem] = []
    for proposed in proposal.items:
        result = _validate_evidence(proposed, text, record)
        if isinstance(result, RejectedItem):
            rejected.append(result)
            continue
        validated.append(
            result.model_copy(update={"extracted_by": f"{response.provider}/{response.model}"})
        )
    counts = persist_items(validated, storage)
    accepted = counts.accepted
    skipped = counts.skipped_duplicates
    merged = counts.evidence_merged
    conflicts = counts.conflicts

    career_json, career_md = render_career(
        storage,
        config,
        run_meta={
            "provider": response.provider,
            "model": response.model,
            "prompt_version": PROMPT_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
        },
    )
    _logger.info(
        "ingest source=%s reused=%s provider=%s model=%s prompt=%s accepted=%d skipped=%d"
        " merged=%d conflicts=%d rejected=%d latency_ms=%d input_tokens=%s output_tokens=%s",
        record.record_id,
        reused,
        response.provider,
        response.model,
        PROMPT_VERSION,
        accepted,
        skipped,
        merged,
        conflicts,
        len(rejected),
        response.latency_ms,
        response.input_tokens,
        response.output_tokens,
    )
    return IngestReport(
        source_record_id=record.record_id,
        source_reused=reused,
        accepted=accepted,
        skipped_duplicates=skipped,
        evidence_merged=merged,
        conflicts=conflicts,
        rejected=rejected,
        career_json_path=career_json,
        career_md_path=career_md,
        provider=response.provider,
        model=response.model,
        prompt_version=PROMPT_VERSION,
        latency_ms=response.latency_ms,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )
