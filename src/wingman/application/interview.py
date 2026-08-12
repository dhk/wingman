"""Interview evidence: stimulus -> reaction -> reasoning, and nomination ->
reasoning (docs/PROFILE-BOOTSTRAP-DESIGN.md).

Two capture mechanics share one entry point, `capture_interview_reaction`,
dispatched by `subtype`:

- **Reaction** (v0, Alignment of perspective): the user submits a piece of
  content (an https:// URL, or a local PDF/DOCX/Markdown/text file), states
  agree/disagree, and explains why. The content is fetched only far enough
  to extract a title and a content hash for provenance.
- **Nomination** (v1, Values, Mission alignment, Network admired): the
  user names a person, organization, or (Network admired) a LinkedIn
  profile URL, and explains why — no fetch at all, there is nothing to
  reduce (LinkedIn is never scraped; the URL is captured as an
  identifier, not dereferenced). The capture note is written to the
  inbox — qa_capture.py's exact pattern — so 'why' and, for Mission
  alignment, the primary-purpose answer both stay retrievable there.
  Mission alignment captures what the person understands the nominated
  org's primary purpose to be ("Pepsi sells cola") as context alongside
  the reasoning, never as evidence.

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

Values-alignment feedback loop v1 (RFC-049, issue #240): every Values and
Mission-alignment nomination also carries `intensity` (a
`SentimentIntensity` scale — 'mild'/'moderate'/'strong' — layered ON TOP OF
the pro/con polarity the subtype naming already carries) and, for the
company-naming subtypes specifically (`values_fallback_*`,
`mission_alignment_*`), `company_reason` (a fixed
`CompanyReasonCategory` — 'company'/'product'/'industry' — captured
ALONGSIDE the free-text 'why', never replacing it). Unlike
`primary_purpose`, both are persisted directly on the `ProfileItem`
itself, not just the inbox note, so a later value-dimension inference pass
(v2, not built here) has structured fields to read.

The value statement (RFC-057, issue #343): every subtype that takes
`intensity` also takes `value_statement` — "what does that tell us you
value?", asked after the 'why' and before the intensity card, captured in
the user's own words. A nomination on its own records a VERDICT about
somebody else; half of the Values and Mission-alignment sections are
condemnations by design, and a condemnation only reaches the profile as
an inference about its target. This field is the positive value statement
behind it ("I value people having the information they need to choose"),
so `application/values.py`'s axis inference reads evidence that is
positive BY CONSTRUCTION rather than judging which way a condemnation
cuts. It augments RFC-056's per-item `direction` rather than replacing it
— every capture predating this field has none, and inference must keep
working without one.
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
from wingman.domain.profile import (
    CompanyReasonCategory,
    EvidenceSpan,
    ItemStatus,
    ProfileItem,
    ProfileItemKind,
    SentimentIntensity,
)
from wingman.domain.provenance import FORM_ARRIVAL, FORM_EXTRACTOR, ClaimClassification
from wingman.domain.source_record import SourceRecord
from wingman.infrastructure.config import Config
from wingman.infrastructure.fetch import FetchError, fetch_url
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.interview")

INTERVIEW_SOURCE_TYPE = "interview_stimulus"
INTERVIEW_PROMPT_VERSION = "interview_capture_v0"
INTERVIEW_EXTRACTOR = "user"  # the user's own words — no model proposed anything

# A capture that arrived through the interview FORM (#287) rather than
# through a conversation: the person answered wingman's own questions
# offline, and an operator ingested the export into their workspace. Still
# their own words, still first-person, still FACT — the difference is HOW
# it arrived, and it is recorded in two places so it cannot be lost: the
# source record's type (the durable provenance row) and the item's
# extracted_by (what every profile surface reads). Somebody looking at
# their own profile a year later can then tell the answers they wrote in a
# form months ago from the ones they said out loud.
FORM_SOURCE_TYPE = "interview_form"

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
# Network admired (v1): nominate a first-degree LinkedIn connection you
# admire, by profile URL — pro-only, no con counterpart. Distinct from
# Values (admiration by name, no networking angle): the target here is a
# dereferenceable identifier specifically because it doubles as a
# warm-path candidate, e.g. for docs/COACHING-MODE-DESIGN.md's "who among
# everyone I know might be valuable for this persona" cross-referencing —
# still a NOMINATION shape (no fetch; LinkedIn is never scraped), the
# target is just a URL instead of a bare name.
NETWORK_ADMIRED_SUBTYPES = {
    "network_admired",
}
NOMINATION_SUBTYPES = VALUES_SUBTYPES | MISSION_ALIGNMENT_SUBTYPES | NETWORK_ADMIRED_SUBTYPES
VALID_SUBTYPES = REACTION_SUBTYPES | NOMINATION_SUBTYPES

# Sentiment-intensity scale (RFC-049, issue #240 v1): required for every
# Values and Mission-alignment subtype — a strength dimension layered on
# top of the pro/con polarity subtype naming already carries. NOT
# alignment_of_perspective (its own agree/disagree shape) or
# network_admired (pro-only, no polarity to scale against).
SENTIMENT_INTENSITY_SUBTYPES = VALUES_SUBTYPES | MISSION_ALIGNMENT_SUBTYPES

# The value-statement question (RFC-057, issue #343) — "what does that
# tell us you value?" — is asked for exactly the subtypes that take an
# intensity, and defined as that set rather than duplicating its members:
# both answer the same question about which captures are a values signal
# at all. alignment_of_perspective_* (a reaction to content, not a
# nomination) and network_admired (a warm-path identifier, not a values
# signal) are outside it, by the same reasoning that keeps intensity out.
VALUE_STATEMENT_SUBTYPES = SENTIMENT_INTENSITY_SUBTYPES

# Company reason taxonomy (RFC-049, issue #240 v1): required for every
# subtype that names a COMPANY — the values fallback and Mission
# alignment pairs — never the people-naming subtypes (values_pro/con,
# network_admired), which don't name a company at all.
COMPANY_REASON_SUBTYPES = {
    "values_fallback_pro",
    "values_fallback_con",
    "mission_alignment_pro",
    "mission_alignment_con",
}

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


def interview_document_key(subtype: str, target: str, persona_id: str | None = None) -> str:
    """One evolving 'document' per (subtype, target[, persona]) tuple —
    capturing the same target again under the same subtype (for the same
    persona, or the coach's own work) supersedes it (RFC-028).
    persona_id=None keeps the exact key shape every capture had before
    coaching mode existed — backward compatible with document_keys
    already in storage; a set persona_id gets its own, distinct key so
    the coach nominating "Jane Goodall" for themselves and for Mike don't
    supersede each other."""
    key = f"interview: {subtype}: " + " ".join(target.strip().lower().split())
    if persona_id is not None:
        key += f": persona:{persona_id}"
    return key


class InterviewReactionReport(BaseModel):
    subtype: str
    target: str
    title: str | None
    outcome: str
    counts: ItemCounts
    intensity: SentimentIntensity | None = None
    company_reason: CompanyReasonCategory | None = None
    value_statement: str = ""


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


def _item_name(subtype: str, target: str, persona_id: str | None) -> str:
    """The ProfileItem.name (and thus its dedup/conflict name_key) for one
    capture. persona_id=None keeps the exact shape every capture had
    before coaching mode existed. A set persona_id gets a distinct name —
    without this, persist_items' name_key-based find_active_item would
    match the coach's own "values_pro: Jane Goodall" against a persona's
    identically-named capture and incorrectly merge/conflict/supersede
    across scopes, the same collision interview_document_key already
    guards against on the source-record side."""
    name = f"{subtype}: {target}"
    if persona_id is not None:
        name += f" (persona:{persona_id})"
    return name


def _provenance_for(
    persona_id: str | None, persona_authored: bool, via_form: bool = False
) -> tuple[ClaimClassification, float, str]:
    """(classification, confidence, extracted_by) for one capture
    (docs/COACHING-MODE-DESIGN.md).

    persona_id=None (the coach's own work — every capture before coaching
    mode existed, unchanged): FACT/1.0/"user", exactly as always.
    persona_id set and persona_authored=True (the persona is literally
    answering themselves, e.g. dictating while the coach types): also a
    verified first-person statement, just a different first person —
    FACT/1.0/"persona". persona_id set and persona_authored=False (the
    default): the coach's own speculation on the persona's behalf ("how
    would Mike answer this") — never a verified statement, so
    HYPOTHESIS/0.6/"coach", distinguishable from either FACT case forever,
    not just at capture time.

    via_form (#287) is the same first person answering wingman's own
    questions in a form instead of a conversation — FACT/1.0 like any
    other answer of their own, marked "form" so the arrival route survives
    on the item itself. It is only honoured for the coach's own scope: a
    form is completed by the tenant, never on somebody else's behalf, so
    combining it with a persona would assert a first-person form answer
    for a person who never saw the form.
    """
    if persona_id is None:
        return ClaimClassification.FACT, 1.0, FORM_EXTRACTOR if via_form else INTERVIEW_EXTRACTOR
    if persona_authored:
        return ClaimClassification.FACT, 1.0, "persona"
    return ClaimClassification.HYPOTHESIS, 0.6, "coach"


def capture_interview_reaction(
    subtype: str,
    target: str,
    why: str,
    config: Config,
    storage: Storage,
    fetcher: Callable[[str], bytes] | None = None,
    primary_purpose: str | None = None,
    intensity: str | None = None,
    company_reason: str | None = None,
    value_statement: str | None = None,
    persona_id: str | None = None,
    persona_authored: bool = False,
    via_form: bool = False,
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

    'intensity' (RFC-049, issue #240 v1) is one of SentimentIntensity's
    values ('mild', 'moderate', 'strong') — a strength dimension layered
    ON TOP OF the polarity the subtype's own '_pro'/'_con' suffix already
    carries. The calling agent's PROTOCOL is to ask for it on every Values
    and Mission-alignment capture (values_pro/con, values_fallback_pro/con,
    mission_alignment_pro/con) — see this function's caller's own docstring
    for the exact follow-up question — but it is not code-enforced here,
    the same protocol-not-code-enforcement status as con-then-pro ordering
    below: an omitted intensity leaves the stored item's `intensity` field
    None rather than blocking the capture, so a client that hasn't caught
    up to this new question yet never breaks. 'company_reason' is one of
    CompanyReasonCategory's values ('company', 'product', 'industry') —
    is this nomination about the company itself, what it makes, or the
    industry it's in? Same protocol status, scoped to the subtypes that
    name a COMPANY specifically (values_fallback_pro/con,
    mission_alignment_pro/con) — never asked for values_pro/con or
    network_admired, which name people, not companies. Both, when given,
    are persisted directly on the resulting ProfileItem (unlike
    primary_purpose, which lives only in the inbox note) so a later
    inference pass has structured fields to read, not just prose. A
    non-empty value that doesn't match the enum IS rejected, regardless of
    subtype — this only tolerates *absence*, never a garbled answer.

    'value_statement' (RFC-057, issue #343) is what this nomination tells
    the person they VALUE, in their own words — free text, no enum, asked
    AFTER the 'why' and BEFORE the intensity card, for the same subtypes
    intensity covers (VALUE_STATEMENT_SUBTYPES). "What does that tell us
    you value?" turns a verdict about somebody else into a positive
    statement about the person answering, which is what the axis inference
    actually needs; see this function's caller's docstring for the exact
    question and its one re-ask. Same protocol-not-code-enforcement status
    as the two fields above — an omitted statement leaves the stored
    item's `value_statement` empty rather than blocking the capture, which
    is also how every capture predating this question reads. Unlike them
    there is nothing to reject: any non-empty text is the person's own
    words, and tidying or validating it would be exactly the paraphrasing
    the BP-06 echo exists to prevent.

    persona_id scopes this capture to a Persona (docs/COACHING-MODE-
    DESIGN.md) instead of the coach's own work — None (the default) is
    unchanged, existing behavior. persona_authored distinguishes the
    persona's own verified words (True) from the coach's speculation on
    their behalf (False, the default) — see _provenance_for.

    via_form (#287) says this answer arrived through the interview form an
    operator ingested, rather than through a conversation. It changes
    provenance and nothing else: the same validation, the same 'why is the
    only evidence' rule, the same RFC-028 supersession — which is exactly
    why application/form_ingest.py calls THIS function instead of writing
    its own path that could drift from these rules. The route is recorded
    on the source record's type, in the inbox note, and on the item's
    extracted_by; see FORM_SOURCE_TYPE.
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

    intensity_raw = (intensity or "").strip().lower()
    intensity_value: SentimentIntensity | None = None
    if intensity_raw:
        try:
            intensity_value = SentimentIntensity(intensity_raw)
        except ValueError as exc:
            valid = ", ".join(level.value for level in SentimentIntensity)
            raise IngestError(f"unknown intensity {intensity_raw!r}; use one of: {valid}.") from exc

    # Free text, so there is no enum to check — only the same emptiness
    # normalization every other optional prose field here gets.
    value_statement = (value_statement or "").strip()

    company_reason_raw = (company_reason or "").strip().lower()
    company_reason_value: CompanyReasonCategory | None = None
    if company_reason_raw:
        try:
            company_reason_value = CompanyReasonCategory(company_reason_raw)
        except ValueError as exc:
            valid = ", ".join(reason.value for reason in CompanyReasonCategory)
            raise IngestError(
                f"unknown company_reason {company_reason_raw!r}; use one of: {valid}."
            ) from exc

    active_for_subtype = [
        item
        for item in storage.list_profile_items()
        if item.kind is ProfileItemKind.INTERVIEW
        and item.subtype == subtype
        and item.status is ItemStatus.ACTIVE
        and item.persona_id == persona_id
    ]
    is_new_target = not any(
        item.name == _item_name(subtype, target, persona_id) for item in active_for_subtype
    )
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
    if value_statement:
        content += f"\nWhat this says they value: {value_statement}\n"
    if intensity_value is not None:
        content += f"\nIntensity: {intensity_value.value}\n"
    if company_reason_value is not None:
        content += f"\nCompany reason: {company_reason_value.value}\n"
    if persona_id is not None:
        content += f"\nPersona: {persona_id}\n"
    if via_form:
        # Part of the hashed content, not decoration: a form answer and the
        # same sentence typed in conversation are different arrivals and
        # must be different source records, so the later one supersedes the
        # earlier through the ordinary lineage instead of colliding with it.
        content += f"\nAnswered in: {FORM_ARRIVAL}\n"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    record = storage.get_source_record_by_hash(content_hash)
    source_type = FORM_SOURCE_TYPE if via_form else INTERVIEW_SOURCE_TYPE

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
            kind = "interview-form-note" if via_form else "interview-note"
            path = config.inbox_dir / f"{stamp}-{kind}.md"
            path.write_text(content, encoding="utf-8")
            data_root = config.data_dir.resolve()
            resolved = path.resolve()
            locator = (
                str(resolved.relative_to(data_root))
                if resolved.is_relative_to(data_root)
                else str(resolved)
            )
            record = SourceRecord(
                source_type=source_type,
                source_locator=locator,
                content_hash=content_hash,
                document_key=interview_document_key(subtype, target, persona_id),
            )
            storage.add_source_record(record)
    else:
        _text, title = _fetch_stimulus(target, fetcher)
        if record is None:
            record = SourceRecord(
                source_type=source_type,
                source_locator=target,
                content_hash=content_hash,
                document_key=interview_document_key(subtype, target, persona_id),
            )
            storage.add_source_record(record)

    classification, confidence, extracted_by = _provenance_for(
        persona_id, persona_authored, via_form
    )
    item = ProfileItem(
        kind=ProfileItemKind.INTERVIEW,
        subtype=subtype,
        persona_id=persona_id,
        name=_item_name(subtype, target, persona_id),
        detail=why,
        classification=classification,
        confidence=confidence,
        intensity=intensity_value,
        company_reason=company_reason_value,
        value_statement=value_statement,
        evidence=[EvidenceSpan(source_record_id=record.record_id, quote=why)],
        prompt_version=INTERVIEW_PROMPT_VERSION,
        extracted_by=extracted_by,
    )
    earlier = storage.record_ids_for_document(
        interview_document_key(subtype, target, persona_id), exclude_record_id=record.record_id
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
        subtype=subtype,
        target=target,
        title=title,
        outcome=outcome,
        counts=counts,
        intensity=intensity_value,
        company_reason=company_reason_value,
        value_statement=value_statement,
    )


def render_interview_reaction(report: InterviewReactionReport) -> str:
    title = f" ({report.title})" if report.title else ""
    tags = []
    if report.intensity is not None:
        tags.append(report.intensity.value)
    if report.company_reason is not None:
        tags.append(report.company_reason.value)
    tag_str = f" [{', '.join(tags)}]" if tags else ""
    line = f"{report.subtype}: {report.target}{title}{tag_str} — {report.outcome}"
    # Prose, so it gets its own line rather than joining the bracketed
    # enum tags — and it is quoted, because it is the person's own words.
    if report.value_statement:
        line += f'\n  values: "{report.value_statement}"'
    return line


def list_interview_documents(
    storage: Storage, persona_id: str | None = None
) -> list[InterviewDocument]:
    """Every active interview capture in scope, read back as a
    build_own_pov candidate document — the read side of every subtype
    captured above.

    persona_id=None (the default) is the coach's own captures, exactly as
    before coaching mode existed. A set persona_id returns ONLY that
    persona's own captures — never the coach's, never another persona's —
    docs/COACHING-MODE-DESIGN.md's "my evidence and their point of view
    never mix," extended to this axis.

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
        if item.kind is ProfileItemKind.INTERVIEW
        and item.status is ItemStatus.ACTIVE
        and item.persona_id == persona_id
    ]


def _is_active_capture(item: ProfileItem, subtype: str, persona_id: str | None) -> bool:
    """The one predicate for 'does this item count as an active capture of
    this subtype[, persona]' — both _active_capture_count (one subtype,
    fetches its own list) and subtype_status (every subtype, one shared
    list) apply it, so the two can never drift on what counts as
    'captured'."""
    return (
        item.kind is ProfileItemKind.INTERVIEW
        and item.subtype == subtype
        and item.status is ItemStatus.ACTIVE
        and item.persona_id == persona_id
    )


def _active_capture_count(storage: Storage, subtype: str, persona_id: str | None = None) -> int:
    """Active capture count for one subtype[, persona] — the counting
    logic subtype_progress and subtype_status both need, kept in exactly
    one place."""
    return sum(
        1 for item in storage.list_profile_items() if _is_active_capture(item, subtype, persona_id)
    )


def subtype_progress(
    storage: Storage, subtype: str, persona_id: str | None = None
) -> tuple[int, int]:
    """(active capture count for this subtype[, persona], the per-subtype
    cap) — lets a caller surface UX-0001's BP-05 position ("N of M
    captured") without reaching into the cap check's own internals."""
    return _active_capture_count(storage, subtype, persona_id=persona_id), (
        _max_submissions_per_subtype()
    )


class SubtypeStatus(BaseModel):
    """One VALID_SUBTYPES entry's status: whether anything is captured
    for it yet, and its count against the per-subtype cap — the same
    (count, cap) pair subtype_progress reports for one subtype at a time,
    here for every subtype at once (issue #311)."""

    subtype: str
    captured: bool
    count: int
    cap: int


def subtype_status(storage: Storage, persona_id: str | None = None) -> dict[str, SubtypeStatus]:
    """Every VALID_SUBTYPES entry's status in one call, scoped to
    persona_id (None = the coach's own work) — the complete picture
    capture_progress_summary's three-bucket summary doesn't give (it
    omits network_admired, and reports category totals rather than
    per-subtype standing).

    One storage.list_profile_items() call, not nine: subtype_progress
    (and _active_capture_count under it) is built for checking a single
    subtype and re-fetches every time, which is the right cost for that
    one-subtype call site (interview_react's own response) but would be
    9 full table scans here. subtype_status instead fetches the list once
    and applies _is_active_capture per subtype in memory — the same
    predicate _active_capture_count uses, so the two still never drift
    apart on what counts as 'captured'."""
    items = storage.list_profile_items()
    cap = _max_submissions_per_subtype()
    result: dict[str, SubtypeStatus] = {}
    for subtype in VALID_SUBTYPES:
        count = sum(1 for item in items if _is_active_capture(item, subtype, persona_id))
        result[subtype] = SubtypeStatus(subtype=subtype, captured=count > 0, count=count, cap=cap)
    return result


def capture_progress_summary(storage: Storage, persona_id: str | None = None) -> str | None:
    """One-line summary of what's been captured so far, across every
    interview category, scoped to persona_id (None = the coach's own) —
    None if nothing has been captured yet in that scope. Used by
    perspectives_start (UX-0001 §4) to decide whether to offer, and how to
    describe, a 'pick up where I left off' option."""
    items = [
        item
        for item in storage.list_profile_items()
        if item.kind is ProfileItemKind.INTERVIEW
        and item.status is ItemStatus.ACTIVE
        and item.persona_id == persona_id
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


# Category grouping for render_interview_status, in UX-0001's own
# category order (Reaction, Values, Mission alignment, Network admired) —
# an explicit ordered list rather than iterating the *_SUBTYPES sets
# directly, since set iteration order isn't guaranteed and this render
# needs a stable, readable one. Every VALID_SUBTYPES member appears in
# exactly one group here — unlike capture_progress_summary, which omits
# network_admired entirely (issue #311).
_STATUS_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Reaction", ("alignment_of_perspective_agree", "alignment_of_perspective_disagree")),
    ("Values", ("values_pro", "values_con", "values_fallback_pro", "values_fallback_con")),
    ("Mission alignment", ("mission_alignment_pro", "mission_alignment_con")),
    ("Network admired", ("network_admired",)),
)


def render_interview_status(status: dict[str, SubtypeStatus]) -> str:
    """The complete per-subtype capture picture, one line per
    VALID_SUBTYPES entry grouped by category (Reaction / Values / Mission
    alignment / Network admired) — mirrors render_profile_listing's
    "Category:" + indented-lines style. Complete by construction: every
    _STATUS_CATEGORIES entry is asserted against VALID_SUBTYPES in tests,
    so a new subtype added to one but not the other would break loudly
    rather than silently going missing here the way capture_progress_summary
    currently omits network_admired."""
    lines: list[str] = []
    for category, subtypes in _STATUS_CATEGORIES:
        lines.append(f"{category}:")
        for subtype in subtypes:
            entry = status[subtype]
            marker = "captured" if entry.captured else "not yet"
            lines.append(f"  {subtype}: {entry.count}/{entry.cap} ({marker})")
    return "\n".join(lines)
