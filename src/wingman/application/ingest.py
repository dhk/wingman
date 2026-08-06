"""Resume ingestion: the Phase 1 slice.

Deterministic ingestion and validation bracket the one model step:
read/hash/persist SourceRecord -> model extraction -> evidence validation ->
conflict detection -> persist -> render career.json / career.md.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from wingman.agents.profile_curator import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_prompt,
    parse_proposal,
)
from wingman.application.evidence import locate_quote
from wingman.application.profile_store import persist_items
from wingman.domain import SourceRecord
from wingman.domain.source_record import derive_document_key
from wingman.domain.extraction import ProposedItem
from wingman.domain.profile import EvidenceSpan, ProfileItem
from wingman.infrastructure.config import Config
from wingman.infrastructure.fetch import FetchError
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
    updated: int
    retired: int
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
    # Imported here, not at module top: resume_formats needs IngestError from
    # this module, so a top-level import would be circular.
    from wingman.application.resume_formats import extract_resume_text

    text = extract_resume_text(resume_path)
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
        document_key=derive_document_key(resume_path.name),
    )
    storage.add_source_record(record)
    return record, False


def _validate_evidence(
    proposed: ProposedItem, source_text: str, record: SourceRecord
) -> ProfileItem | RejectedItem:
    """Deterministic check: every quote must be non-blank and appear in the source.

    Verbatim modulo whitespace (RFC-026): presentation is forgiven, content
    is not. Line wrapping was the original case; PDF extraction that emits
    no inter-word spaces at all is the case that forced whitespace to be
    ignored outright rather than merely folded (#278). Every non-space
    character must still appear, in order.

    The accepted item cites the SOURCE's span, not the proposed text, so a
    citation always quotes the document.
    """
    resolved: list[str] = []
    for quote in proposed.quotes:
        if not quote.strip():
            return RejectedItem(name=proposed.name, reason="empty evidence quote")
        # The source's own span, matched ignoring whitespace — so a quote of a
        # PDF that extracted without inter-word spaces still resolves, and the
        # stored citation quotes the document rather than the model's readable
        # reconstruction of it (#278).
        found = locate_quote(quote, source_text)
        if found is None:
            return RejectedItem(
                name=proposed.name,
                reason=f"evidence quote not found verbatim in source: {quote[:80]!r}",
            )
        resolved.append(found)
    return ProfileItem(
        kind=proposed.item_kind,
        name=proposed.name,
        detail=proposed.detail,
        company=proposed.company,
        title=proposed.title,
        started=proposed.started,
        ended=proposed.ended,
        classification=proposed.classification,
        confidence=proposed.confidence,
        evidence=[
            EvidenceSpan(source_record_id=record.record_id, quote=quote) for quote in resolved
        ],
        prompt_version=PROMPT_VERSION,
        extracted_by="",
    )


def ingest_resume_from_url(
    url: str,
    config: Config,
    storage: Storage,
    provider: ModelProvider,
    fetcher: Callable[[str], bytes] | None = None,
) -> IngestReport:
    """Fetch a resume from a Google Docs/Drive link, archive it, and ingest it.

    The fetched bytes are written to the inbox first (the original artifact
    is the provenance record), then flow through the ordinary file pipeline.
    """
    from wingman.application.resume_formats import fetch_resume_bytes, suffix_for_bytes

    try:
        name, data = (
            fetch_resume_bytes(url, fetcher) if fetcher is not None else fetch_resume_bytes(url)
        )
    except FetchError as exc:
        raise IngestError(f"{exc}. Nothing was ingested.") from exc
    # Microsecond stamp: two fetches of the same document in the same second
    # must archive as two artifacts, never overwrite one another.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    path = config.inbox_dir / f"{stamp}-{name}{suffix_for_bytes(data)}"
    try:
        path.write_bytes(data)
    except OSError as exc:
        raise IngestError(
            f"could not archive the fetched document to {path} ({exc}). Nothing was ingested."
        ) from exc
    return ingest_resume(path, config, storage, provider)


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
    # Earlier versions of the same document (RFC-028): their claims are
    # replaced by this version's, never piled up as conflicts with it.
    superseded = storage.record_ids_for_document(
        record.document_key, exclude_record_id=record.record_id
    )
    counts = persist_items(validated, storage, superseded_records=superseded)
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
        " merged=%d conflicts=%d updated=%d retired=%d rejected=%d latency_ms=%d"
        " input_tokens=%s output_tokens=%s",
        record.record_id,
        reused,
        response.provider,
        response.model,
        PROMPT_VERSION,
        accepted,
        skipped,
        merged,
        conflicts,
        counts.updated,
        counts.retired,
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
        updated=counts.updated,
        retired=counts.retired,
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
