"""Interview evidence: stimulus -> reaction -> reasoning, and nomination ->
reasoning (docs/PROFILE-BOOTSTRAP-DESIGN.md).

Two capture mechanics share one entry point, `capture_interview_reaction`,
dispatched by `subtype`:

- **Reaction** (v0, Alignment of perspective): the user submits a piece of
  content (an https:// URL, or a local PDF/DOCX/Markdown/text file), states
  agree/disagree, and explains why. The content is fetched only far enough
  to extract a title and a content hash for provenance.
- **Nomination** (v1, Values and Mission alignment): the user names a
  person or organization and explains why — no fetch at all, there is
  nothing to reduce. A nomination's target is a bare name, not a
  dereferenceable location, so (unlike a reaction) the capture note is
  written to the inbox — qa_capture.py's exact pattern — so 'why' and, for
  Mission alignment, the primary-purpose answer both stay retrievable
  there. Mission alignment captures what the person understands the
  nominated org's primary purpose to be ("Pepsi sells cola") as context
  alongside the reasoning, never as evidence.

Either way, the "why" is the ONLY thing that ever becomes evidence,
mirroring qa_capture.py's pattern exactly — the stimulus/nominee/purpose
answer is context, never quoted as if it were the user's own words (the
design's "one hard rule"). No model call is made anywhere in this module,
so the model-exposure risk the design names doesn't arise yet; that's v1's
synthesis step (InterviewDocument, not built here).

Ordering (con-then-pro, ask-#2-first) is enforced the same way this
codebase already enforces other tool-usage protocols (qa_capture,
resolve_requirement) — as instructions in the MCP tool's docstring for the
calling agent to follow, not a stateful wizard here.

`list_interview_documents` is the read side: every active capture, back as
a `domain.interview.InterviewDocument` for `application.pov.build_own_pov`
to read alongside `ExternalDocument`/`CorpusDocument` — the final v1 slice,
actually synthesizing these captures into a stance.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from wingman.application.ingest import IngestError
from wingman.application.profile_store import ItemCounts, persist_items
from wingman.application.research import extract_page, page_title
from wingman.application.resume_formats import extract_resume_text
from wingman.domain.interview import InterviewDocument
from wingman.domain.profile import EvidenceSpan, ItemStatus, ProfileItem, ProfileItemKind
from wingman.domain.provenance import ClaimClassification
from wingman.domain.source_record import SourceRecord
from wingman.infrastructure.config import Config
from wingman.infrastructure.fetch import FetchError, fetch_url
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.interview")

INTERVIEW_SOURCE_TYPE = "interview_stimulus"
INTERVIEW_PROMPT_VERSION = "interview_capture_v0"
INTERVIEW_EXTRACTOR = "user"  # the user's own words — no model proposed anything

# Alignment of perspective (v0): react to fetched content.
REACTION_SUBTYPES = {
    "alignment_of_perspective_agree",
    "alignment_of_perspective_disagree",
}
# Values (v1): nominate a person by name, no fetch — values_fallback_* name
# a company instead, when someone struggles to name people.
VALUES_SUBTYPES = {
    "values_pro",
    "values_con",
    "values_fallback_pro",
    "values_fallback_con",
}
# Mission alignment (v1): nominate an organization — a company, club, or any
# nominated set of people aligned for a purpose, not "company" specifically.
MISSION_ALIGNMENT_SUBTYPES = {
    "mission_alignment_pro",
    "mission_alignment_con",
}
NOMINATION_SUBTYPES = VALUES_SUBTYPES | MISSION_ALIGNMENT_SUBTYPES
VALID_SUBTYPES = REACTION_SUBTYPES | NOMINATION_SUBTYPES

# values_con excludes Hitler — too easy a nomination to discriminate
# anything about the person's actual values. No analogous exclusion for
# values_fallback_con (companies) or mission_alignment_con — see
# docs/PROFILE-BOOTSTRAP-DESIGN.md's resolution of why.
_EXCLUDED_VALUES_CON_NOMINEES = {"hitler", "adolf hitler"}

# Per-submission size (design doc "Limits and configuration"): an interview
# stimulus is an article, not a resume — KB-scale, well under webui's 20MB
# MAX_UPLOAD_BYTES. Applies to both a fetched URL's body and a local file's
# size, before extraction.
INTERVIEW_MAX_SUBMISSION_BYTES = 2 * 1024 * 1024

# Submission count "per onboarding pass" (design doc "Limits and
# configuration"): scoped per subtype rather than globally, since a global
# cap sized for v0's single reaction pair (~6, "3 agree + 3 disagree") would
# starve v1's Values/Mission alignment categories, which didn't exist when
# that number was chosen. A NEW target for a subtype already at the cap is
# refused; re-capturing an existing target (a supersession, not growth)
# never counts against it.
ENV_INTERVIEW_MAX_PER_SUBTYPE = "WINGMAN_INTERVIEW_MAX_PER_SUBTYPE"
_DEFAULT_MAX_PER_SUBTYPE = 6


def _max_submissions_per_subtype(env: dict[str, str] | None = None) -> int:
    raw = (os.environ if env is None else env).get(ENV_INTERVIEW_MAX_PER_SUBTYPE, "").strip()
    if not raw:
        return _DEFAULT_MAX_PER_SUBTYPE
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_PER_SUBTYPE
    return value if value > 0 else _DEFAULT_MAX_PER_SUBTYPE


def interview_document_key(subtype: str, target: str) -> str:
    """One evolving 'document' per (subtype, target) pair — capturing the
    same target again under the same subtype supersedes it (RFC-028)."""
    return f"interview: {subtype}: " + " ".join(target.strip().lower().split())


class InterviewReactionReport(BaseModel):
    subtype: str
    target: str
    title: str | None
    outcome: str
    counts: ItemCounts


def _fetch_stimulus(target: str, fetcher: Callable[[str], bytes] | None) -> tuple[str, str | None]:
    """(extracted text, title) for a submitted URL or local file path.

    Deterministic extraction only, reusing existing infra as-is — an
    https:// URL is fetched and reduced the same way research.py reduces a
    company page (visible text + title, no JS, no rendering); a local file
    dispatches on suffix through resume_formats.extract_resume_text, the
    same PDF/DOCX/Markdown/text pipeline resume ingestion already uses.
    """
    if target.startswith("https://"):
        fetch = fetcher if fetcher is not None else fetch_url
        try:
            data = fetch(target)
        except FetchError as exc:
            raise IngestError(f"could not fetch {target} ({exc}). Nothing was captured.") from exc
        if len(data) > INTERVIEW_MAX_SUBMISSION_BYTES:
            limit_kb = INTERVIEW_MAX_SUBMISSION_BYTES // 1024
            raise IngestError(
                f"{target} is larger than the {limit_kb}KB interview submission limit "
                "(an article, not a full document). Nothing was captured."
            )
        text, _links = extract_page(data, target)
        if not text.strip():
            raise IngestError(f"{target} had no extractable text. Nothing was captured.")
        return text, page_title(data)
    path = Path(target).expanduser()
    if not path.is_file():
        raise IngestError(
            f"{target!r} is neither an https:// URL nor an existing file. Nothing was captured."
        )
    if path.stat().st_size > INTERVIEW_MAX_SUBMISSION_BYTES:
        limit_kb = INTERVIEW_MAX_SUBMISSION_BYTES // 1024
        raise IngestError(
            f"{path.name} is larger than the {limit_kb}KB interview submission limit "
            "(an article, not a full document). Nothing was captured."
        )
    return extract_resume_text(path), path.name


def capture_interview_reaction(
    subtype: str,
    target: str,
    why: str,
    config: Config,
    storage: Storage,
    fetcher: Callable[[str], bytes] | None = None,
    primary_purpose: str | None = None,
) -> InterviewReactionReport:
    """Persist one interview capture — a reaction to fetched content
    (Alignment of perspective), or a nomination by name (Values, Mission
    alignment).

    'target' is the https:// URL/local file to react to for a reaction
    subtype, or the person/organization name for a nomination subtype.
    Either way 'why' is the only evidence stored, never the target's own
    content. 'primary_purpose' is required for mission_alignment_* only —
    what the person understands the nominated org's primary purpose to be
    ("Pepsi sells cola"); it is written to the inbox note alongside the
    capture (like qa_capture's own note file) so it stays retrievable, but
    it never becomes the evidence quote itself.
    """
    subtype = subtype.strip().lower()
    if subtype not in VALID_SUBTYPES:
        valid = ", ".join(sorted(VALID_SUBTYPES))
        raise IngestError(f"unknown subtype {subtype!r}; use one of: {valid}.")
    target = target.strip()
    why = why.strip()
    if not target or not why:
        raise IngestError("both a target and a why are required — nothing was captured.")
    primary_purpose = (primary_purpose or "").strip() or None
    if subtype in MISSION_ALIGNMENT_SUBTYPES and primary_purpose is None:
        raise IngestError(
            "primary_purpose is required for mission_alignment subtypes — what do you "
            "understand this organization's primary purpose to be? Nothing was captured."
        )

    active_for_subtype = [
        item
        for item in storage.list_profile_items()
        if item.kind is ProfileItemKind.INTERVIEW
        and item.subtype == subtype
        and item.status is ItemStatus.ACTIVE
    ]
    is_new_target = not any(item.name == f"{subtype}: {target}" for item in active_for_subtype)
    max_per_subtype = _max_submissions_per_subtype()
    if is_new_target and len(active_for_subtype) >= max_per_subtype:
        raise IngestError(
            f"{subtype} already has {max_per_subtype} captures — that's the limit per "
            f"onboarding pass ({ENV_INTERVIEW_MAX_PER_SUBTYPE} to change it). Nothing "
            "was captured; re-capturing an existing target still works."
        )

    # Hashed on the full capture (subtype + target + why [+ primary_purpose
    # for mission alignment]), not just the fetched/nominated target —
    # mirroring qa_capture's Q+A hash exactly. A changed 'why' (or purpose
    # answer) must produce a new record, or RFC-028 supersession has
    # nothing earlier to point at: reusing one record per target (keyed on
    # the target's own content) would tie every revised capture to the SAME
    # record, excluding it from its own lineage check and forcing a
    # spurious conflict instead of a clean update.
    content = f"# Interview capture\n\nSubtype: {subtype}\n\nTarget: {target}\n\nWhy: {why}\n"
    if primary_purpose is not None:
        content += f"\nUnderstood primary purpose: {primary_purpose}\n"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    record = storage.get_source_record_by_hash(content_hash)

    if subtype in NOMINATION_SUBTYPES:
        if subtype == "values_con" and " ".join(target.lower().split()) in (
            _EXCLUDED_VALUES_CON_NOMINEES
        ):
            raise IngestError(
                f"{target!r} is excluded from values_con — too easy a nomination to "
                "discriminate anything about your actual values. Nothing was captured."
            )
        title = None
        if record is None:
            # A nomination's target is a bare name, not a real location —
            # unlike a reaction's URL/file, there is nowhere to point
            # source_locator that a reader could later dereference. Write
            # the capture note to the inbox (qa_capture's exact pattern) so
            # 'why' AND primary_purpose both stay retrievable there, not
            # just baked into an opaque hash.
            config.inbox_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
            path = config.inbox_dir / f"{stamp}-interview-note.md"
            path.write_text(content, encoding="utf-8")
            data_root = config.data_dir.resolve()
            resolved = path.resolve()
            locator = (
                str(resolved.relative_to(data_root))
                if resolved.is_relative_to(data_root)
                else str(resolved)
            )
            record = SourceRecord(
                source_type=INTERVIEW_SOURCE_TYPE,
                source_locator=locator,
                content_hash=content_hash,
                document_key=interview_document_key(subtype, target),
            )
            storage.add_source_record(record)
    else:
        _text, title = _fetch_stimulus(target, fetcher)
        if record is None:
            record = SourceRecord(
                source_type=INTERVIEW_SOURCE_TYPE,
                source_locator=target,
                content_hash=content_hash,
                document_key=interview_document_key(subtype, target),
            )
            storage.add_source_record(record)

    item = ProfileItem(
        kind=ProfileItemKind.INTERVIEW,
        subtype=subtype,
        name=f"{subtype}: {target}",
        detail=why,
        classification=ClaimClassification.FACT,  # the user's own stated reaction
        confidence=1.0,  # first-person statement; there is no better source
        evidence=[EvidenceSpan(source_record_id=record.record_id, quote=why)],
        prompt_version=INTERVIEW_PROMPT_VERSION,
        extracted_by=INTERVIEW_EXTRACTOR,
    )
    earlier = storage.record_ids_for_document(
        interview_document_key(subtype, target), exclude_record_id=record.record_id
    )
    counts = persist_items([item], storage, superseded_records=earlier)
    if counts.updated or counts.retired:
        outcome = "updated — the earlier capture for this target was superseded"
    elif counts.skipped_duplicates or counts.evidence_merged:
        outcome = "already captured — nothing changed"
    elif counts.conflicts:
        outcome = "saved as a conflict with an existing item (resolve via wingman profile)"
    else:
        outcome = "saved"
    _logger.info("interview_capture subtype=%s outcome=%s target=%r", subtype, outcome, target)
    return InterviewReactionReport(
        subtype=subtype, target=target, title=title, outcome=outcome, counts=counts
    )


def render_interview_reaction(report: InterviewReactionReport) -> str:
    title = f" ({report.title})" if report.title else ""
    return f"{report.subtype}: {report.target}{title} — {report.outcome}"


def list_interview_documents(storage: Storage) -> list[InterviewDocument]:
    """Every active interview capture, read back as a build_own_pov
    candidate document — the read side of every subtype captured above.

    body is ALWAYS item.detail (the 'why'), never anything derived from the
    target/stimulus — see InterviewDocument's own docstring for why that is
    what keeps this synthesis-safe. primary_purpose is deliberately NOT
    included here even for mission_alignment items: it lives only in the
    inbox note as durable provenance, not fed to the model in this slice —
    keeps 'why is the only evidence' unambiguous rather than reopening it.
    """
    return [
        InterviewDocument(
            doc_id=item.item_id,
            title=item.name,
            body=item.detail,
            published_at=item.extracted_at,
            source_record_id=item.evidence[0].source_record_id,
        )
        for item in storage.list_profile_items()
        if item.kind is ProfileItemKind.INTERVIEW and item.status is ItemStatus.ACTIVE
    ]


def subtype_progress(storage: Storage, subtype: str) -> tuple[int, int]:
    """(active capture count for this subtype, the per-subtype cap) — lets a
    caller surface UX-0001's BP-05 position ("N of M captured") without
    reaching into the cap check's own internals."""
    count = sum(
        1
        for item in storage.list_profile_items()
        if item.kind is ProfileItemKind.INTERVIEW
        and item.subtype == subtype
        and item.status is ItemStatus.ACTIVE
    )
    return count, _max_submissions_per_subtype()


def capture_progress_summary(storage: Storage) -> str | None:
    """One-line summary of what's been captured so far, across every
    interview category — None if nothing has been captured yet. Used by
    perspectives_start (UX-0001 §4) to decide whether to offer, and how to
    describe, a 'pick up where I left off' option."""
    items = [
        item
        for item in storage.list_profile_items()
        if item.kind is ProfileItemKind.INTERVIEW and item.status is ItemStatus.ACTIVE
    ]
    if not items:
        return None
    reactions = sum(1 for item in items if item.subtype in REACTION_SUBTYPES)
    people = sum(1 for item in items if item.subtype in VALUES_SUBTYPES)
    orgs = sum(1 for item in items if item.subtype in MISSION_ALIGNMENT_SUBTYPES)
    parts = []
    if reactions:
        parts.append(f"{reactions} reaction{'s' if reactions != 1 else ''}")
    if people:
        parts.append(f"{people} Values nomination{'s' if people != 1 else ''}")
    if orgs:
        parts.append(f"{orgs} Mission alignment nomination{'s' if orgs != 1 else ''}")
    return ", ".join(parts)
