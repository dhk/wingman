"""Interview-reaction evidence: stimulus -> reaction -> reasoning.

v0 of docs/PROFILE-BOOTSTRAP-DESIGN.md — a way to bootstrap quote-backed
profile evidence without requiring pre-existing published writing. One
reaction is captured at a time: the user submits a piece of content (an
https:// URL, or a local PDF/DOCX/Markdown/text file), states whether they
agree or disagree with it, and explains why in their own words. The
submitted content is fetched only far enough to extract a title and a
content hash for provenance — the "why" is the only thing that ever
becomes evidence, mirroring qa_capture.py's pattern exactly. A model must
never be able to quote the stimulus as if it were the user's own words
(the design's "one hard rule"); v0 makes no model call at all, so that
risk doesn't arise yet.

v0 ships Alignment of perspective only (agree/disagree). Values and
Mission alignment are v1, along with synthesizing these reactions into a
stance via a new InterviewDocument candidate type — neither exists yet.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from wingman.application.ingest import IngestError
from wingman.application.profile_store import ItemCounts, persist_items
from wingman.application.research import extract_page, page_title
from wingman.application.resume_formats import extract_resume_text
from wingman.domain.profile import EvidenceSpan, ProfileItem, ProfileItemKind
from wingman.domain.provenance import ClaimClassification
from wingman.domain.source_record import SourceRecord
from wingman.infrastructure.fetch import FetchError, fetch_url
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.interview")

INTERVIEW_SOURCE_TYPE = "interview_stimulus"
INTERVIEW_PROMPT_VERSION = "interview_capture_v0"
INTERVIEW_EXTRACTOR = "user"  # the user's own words — no model proposed anything

# v0 ships Alignment of perspective only; Values/Mission alignment subtypes
# (values_pro, values_con, values_fallback_pro, values_fallback_con,
# mission_alignment_pro, mission_alignment_con, ...) are added in v1.
VALID_SUBTYPES = {
    "alignment_of_perspective_agree",
    "alignment_of_perspective_disagree",
}


def interview_document_key(subtype: str, stimulus: str) -> str:
    """One evolving 'document' per (subtype, stimulus) pair: reacting to the
    same content again under the same subtype supersedes it (RFC-028)."""
    return f"interview: {subtype}: " + " ".join(stimulus.strip().lower().split())


class InterviewReactionReport(BaseModel):
    subtype: str
    stimulus: str
    title: str | None
    outcome: str
    counts: ItemCounts


def _fetch_stimulus(
    stimulus: str, fetcher: Callable[[str], bytes] | None
) -> tuple[str, str | None]:
    """(extracted text, title) for a submitted URL or local file path.

    Deterministic extraction only, reusing existing infra as-is — an
    https:// URL is fetched and reduced the same way research.py reduces a
    company page (visible text + title, no JS, no rendering); a local file
    dispatches on suffix through resume_formats.extract_resume_text, the
    same PDF/DOCX/Markdown/text pipeline resume ingestion already uses.
    """
    if stimulus.startswith("https://"):
        fetch = fetcher if fetcher is not None else fetch_url
        try:
            data = fetch(stimulus)
        except FetchError as exc:
            raise IngestError(f"could not fetch {stimulus} ({exc}). Nothing was captured.") from exc
        text, _links = extract_page(data, stimulus)
        if not text.strip():
            raise IngestError(f"{stimulus} had no extractable text. Nothing was captured.")
        return text, page_title(data)
    path = Path(stimulus).expanduser()
    if not path.is_file():
        raise IngestError(
            f"{stimulus!r} is neither an https:// URL nor an existing file. Nothing was captured."
        )
    return extract_resume_text(path), path.name


def capture_interview_reaction(
    subtype: str,
    stimulus: str,
    why: str,
    storage: Storage,
    fetcher: Callable[[str], bytes] | None = None,
) -> InterviewReactionReport:
    """Persist one stimulus -> reaction -> reasoning capture.

    The stimulus is fetched only for provenance (title, content hash);
    'why' is the only evidence stored, never the stimulus's own content.
    """
    subtype = subtype.strip().lower()
    if subtype not in VALID_SUBTYPES:
        valid = ", ".join(sorted(VALID_SUBTYPES))
        raise IngestError(f"unknown subtype {subtype!r}; use one of: {valid}.")
    stimulus = stimulus.strip()
    why = why.strip()
    if not stimulus or not why:
        raise IngestError("both a stimulus and a why are required — nothing was captured.")

    _text, title = _fetch_stimulus(stimulus, fetcher)
    # Hashed on the full reaction (subtype + stimulus + why), not just the
    # fetched stimulus text — mirroring qa_capture's Q+A hash exactly. A
    # changed 'why' must produce a new record, or RFC-028 supersession has
    # nothing earlier to point at: reusing one record per stimulus (keyed
    # on the stimulus's own content) would tie every revised reaction to
    # the SAME record, excluding it from its own lineage check and forcing
    # a spurious conflict instead of a clean update.
    content = f"# Interview reaction\n\nSubtype: {subtype}\n\nStimulus: {stimulus}\n\nWhy: {why}\n"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    record = storage.get_source_record_by_hash(content_hash)
    if record is None:
        record = SourceRecord(
            source_type=INTERVIEW_SOURCE_TYPE,
            source_locator=stimulus,
            content_hash=content_hash,
            document_key=interview_document_key(subtype, stimulus),
        )
        storage.add_source_record(record)

    item = ProfileItem(
        kind=ProfileItemKind.INTERVIEW,
        subtype=subtype,
        name=f"{subtype}: {stimulus}",
        detail=why,
        classification=ClaimClassification.FACT,  # the user's own stated reaction
        confidence=1.0,  # first-person statement; there is no better source
        evidence=[EvidenceSpan(source_record_id=record.record_id, quote=why)],
        prompt_version=INTERVIEW_PROMPT_VERSION,
        extracted_by=INTERVIEW_EXTRACTOR,
    )
    earlier = storage.record_ids_for_document(
        interview_document_key(subtype, stimulus), exclude_record_id=record.record_id
    )
    counts = persist_items([item], storage, superseded_records=earlier)
    if counts.updated or counts.retired:
        outcome = "updated — the earlier reaction to this stimulus was superseded"
    elif counts.skipped_duplicates or counts.evidence_merged:
        outcome = "already captured — nothing changed"
    elif counts.conflicts:
        outcome = "saved as a conflict with an existing item (resolve via wingman profile)"
    else:
        outcome = "saved"
    _logger.info("interview_reaction subtype=%s outcome=%s stimulus=%r", subtype, outcome, stimulus)
    return InterviewReactionReport(
        subtype=subtype, stimulus=stimulus, title=title, outcome=outcome, counts=counts
    )


def render_interview_reaction(report: InterviewReactionReport) -> str:
    title = f" ({report.title})" if report.title else ""
    return f"{report.subtype}: {report.stimulus}{title} — {report.outcome}"
