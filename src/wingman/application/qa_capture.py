"""Clarifying answers become durable profile evidence (#96, RFC-036).

During assess/pack work the user answers questions that resolve Unknown
verdicts — and the answer used to evaporate with the chat, so the same
gap resurfaced on the next role. capture_qa persists one Q&A pair with
full evidence discipline and zero model calls: the pair is written to
the inbox as a source file (the provenance record), recorded as a
SourceRecord (source_type 'qa_note', document_key 'qa:<question-key>'),
and becomes one ACTIVE ProfileItem whose evidence quote is the user's
answer verbatim — which resolves against the file just written, keeping
evidence-before-assertion intact. Future assess runs cite it like any
other profile item; re-answering the same question supersedes the old
answer through the ordinary RFC-028 lineage machinery instead of piling
up conflicts.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pydantic import BaseModel

from wingman.application.ingest import IngestError
from wingman.application.profile_store import ItemCounts, persist_items
from wingman.domain.profile import EvidenceSpan, ProfileItem, ProfileItemKind
from wingman.domain.provenance import ClaimClassification
from wingman.domain.source_record import SourceRecord
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.qa_capture")

QA_SOURCE_TYPE = "qa_note"
QA_PROMPT_VERSION = "qa_capture_v1"
QA_EXTRACTOR = "user"  # the user's own words — no model proposed anything


def qa_document_key(question: str) -> str:
    """One evolving 'document' per question: re-answers supersede (RFC-028)."""
    return "qa: " + " ".join(question.lower().split())


class QaCaptureReport(BaseModel):
    question: str
    kind: str
    outcome: str
    source_path: str
    counts: ItemCounts


DESTINATIONS = ("profile", "answers")

# Preferences are NOT a destination here, deliberately. In-office, travel,
# location and comp belong in the job-criteria document (RFC-035), whose hard
# filters and weighted wants drive opening scoring — and that RFC is explicit
# that criteria are the user's own words and are never written unilaterally.
# Filed as profile items they score nothing and pretend to be skills, which is
# how 'Willing to be in-office 25%+? — Yes' became a skill on a live
# workspace (#283). This module refuses instead of automating a document that
# must be confirmed.
_PREFERENCE_HINT = (
    "preferences (in-office, travel, location, compensation) belong in the "
    "job-criteria document, not the profile — they drive opening scoring "
    "there and score nothing here. Run 'job_criteria' with action='review' "
    "and save the document once the wording is confirmed."
)


def capture_qa(
    question: str,
    answer: str,
    config: Config,
    storage: Storage,
    kind: str = "achievement",
    classification: str = "fact",
    destination: str = "profile",
) -> QaCaptureReport:
    """Persist one Q&A pair — as citable profile evidence, or in the answer bank.

    'profile' (default) is the original behavior: a clarifying answer about
    a claim becomes a ProfileItem with the answer as its evidence.

    'answers' routes to the application answer bank (RFC-030) instead. A
    screening question — 'Have you shipped an AI/LLM product?' — is not a
    claim about the person's career; it is a question an employer asked,
    whose refined answer gets reused across applications. Storing it in the
    profile made a question the *name* of an achievement (#283).
    """
    question = " ".join(question.split())
    answer = answer.strip()
    if not question or not answer:
        raise IngestError("both a question and an answer are required — nothing was saved.")
    target = destination.strip().lower() or "profile"
    if target not in DESTINATIONS:
        raise IngestError(
            f"unknown destination {destination!r}; use one of: {', '.join(DESTINATIONS)}. "
            f"Note that {_PREFERENCE_HINT}"
        )
    if target == "answers":
        from wingman.application.answers import save_answer

        entry, created = save_answer(question, answer, storage)
        return QaCaptureReport(
            question=question,
            kind="answer",
            outcome="saved" if created else "revised",
            source_path=f"answer bank {entry.answer_id[:8]}",
            counts=ItemCounts(),
        )
    try:
        item_kind = ProfileItemKind(kind.strip().lower())
    except ValueError as exc:
        valid = ", ".join(entry.value for entry in ProfileItemKind)
        raise IngestError(f"unknown kind {kind!r}; use one of: {valid}.") from exc
    try:
        claim = ClaimClassification(classification.strip().lower())
    except ValueError as exc:
        valid = ", ".join(entry.value for entry in ClaimClassification)
        raise IngestError(
            f"unknown classification {classification!r}; use one of: {valid}."
        ) from exc

    content = f"# Q&A note\n\nQ: {question}\n\nA: {answer}\n"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    record = storage.get_source_record_by_hash(content_hash)
    if record is None:
        config.inbox_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        path = config.inbox_dir / f"{stamp}-qa-note.md"
        path.write_text(content, encoding="utf-8")
        data_root = config.data_dir.resolve()
        resolved = path.resolve()
        record = SourceRecord(
            source_type=QA_SOURCE_TYPE,
            source_locator=str(resolved.relative_to(data_root))
            if resolved.is_relative_to(data_root)
            else str(resolved),
            content_hash=content_hash,
            document_key=qa_document_key(question),
        )
        storage.add_source_record(record)

    item = ProfileItem(
        kind=item_kind,
        name=question,
        detail=answer,
        classification=claim,
        confidence=1.0,  # first-person statement; there is no better source
        evidence=[EvidenceSpan(source_record_id=record.record_id, quote=answer)],
        prompt_version=QA_PROMPT_VERSION,
        extracted_by=QA_EXTRACTOR,
    )
    earlier = storage.record_ids_for_document(
        qa_document_key(question), exclude_record_id=record.record_id
    )
    counts = persist_items([item], storage, superseded_records=earlier)
    if counts.updated or counts.retired:
        outcome = "updated — the earlier answer to this question was superseded"
    elif counts.skipped_duplicates or counts.evidence_merged:
        outcome = "already captured — nothing changed"
    elif counts.conflicts:
        outcome = "saved as a conflict with an existing item (resolve via wingman profile)"
    else:
        outcome = "saved"
    _logger.info("qa captured kind=%s outcome=%s question=%r", item_kind.value, outcome, question)
    return QaCaptureReport(
        question=question,
        kind=item_kind.value,
        outcome=outcome,
        source_path=record.source_locator,
        counts=counts,
    )
