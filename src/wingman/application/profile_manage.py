"""Career-profile management: list, remove, resolve, amend, correct, clear (RFC-027).

The manual correction workflow Phase 1 promised and never got: re-ingesting
an evolving source-of-truth document accumulates items — the same skill
re-proposed with different wording is stored as a side-by-side conflict
row — and until now nothing short of raw SQL could remove or resolve one.

Every mutation re-renders career.json/career.md, so the artifacts never
drift from the table. Items are addressed by any unambiguous item_id
prefix (git-style), because nobody types a full UUID.

`amend_item` (RFC-071, issue #381) is the one mutation that touches what a
claim SAYS rather than how it is filed, and it is deliberately confined to
interview captures — see its docstring for where that line is and why it
sits there.

`correct_item` (issue #487) is amend's mirror image: for the achievements,
skills, roles and testimonials amend refuses, whose evidence is quoted
verbatim from a document. It does not reopen the "editing a quote" question
amend's docstring settles — it exists for the narrower, real gap that
settlement left: a transcription or voice-dictation error in evidence that
WAS captured correctly, with no path to fix it short of filesystem access to
the workspace inbox (which a hosted tenant does not have). See its docstring
for the guard that keeps it from becoming a second `amend`.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from wingman.application.ingest import IngestError
from wingman.application.interview import AMENDMENT_SOURCE_TYPE
from wingman.domain.profile import (
    CompanyReasonCategory,
    EvidenceSpan,
    ItemRevision,
    ItemStatus,
    ProfileItem,
    ProfileItemKind,
    SentimentIntensity,
)
from wingman.domain.provenance import FORM_EXTRACTOR
from wingman.domain.source_record import SourceRecord
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage
from wingman.reporting.career import render_career

_logger = get_logger("application.profile_manage")


def _rerender(config: Config, storage: Storage) -> None:
    render_career(
        storage,
        config,
        run_meta={
            "provider": "manual",
            "model": "wingman profile",
            "prompt_version": "-",
            "generated_at": datetime.now(UTC).isoformat(),
        },
    )


def find_item(item_id_prefix: str, storage: Storage) -> ProfileItem:
    """Resolve an unambiguous item_id prefix to its item, or fail visibly."""
    prefix = item_id_prefix.strip()
    if not prefix:
        raise IngestError("item id is empty; see 'wingman profile list' for ids.")
    matches = [item for item in storage.list_profile_items() if item.item_id.startswith(prefix)]
    if not matches:
        raise IngestError(f"no profile item with id {prefix!r}; see 'wingman profile list'.")
    if len(matches) > 1:
        shorts = ", ".join(item.item_id[:8] for item in matches)
        raise IngestError(f"id {prefix!r} is ambiguous ({shorts}); use more characters.")
    return matches[0]


def remove_item(item_id_prefix: str, config: Config, storage: Storage) -> ProfileItem:
    """Delete one item (active or conflict) and re-render the career artifacts."""
    item = find_item(item_id_prefix, storage)
    storage.delete_profile_item(item.item_id)
    _rerender(config, storage)
    _logger.info("profile rm id=%s kind=%s name=%s", item.item_id, item.kind.value, item.name)
    return item


def resolve_item(
    item_id_prefix: str, config: Config, storage: Storage
) -> tuple[ProfileItem, list[ProfileItem]]:
    """Keep the given item as the single row for its kind+name; drop its rivals.

    The winner is promoted to active (whether it was the active row or a
    conflict challenger); every other item sharing its kind and name key —
    the old active, other conflict rows — is deleted. Returns the promoted
    winner and the removed rivals.
    """
    winner = find_item(item_id_prefix, storage)
    rivals = [
        item
        for item in storage.list_profile_items()
        if item.item_id != winner.item_id
        and item.kind is winner.kind
        and item.name_key == winner.name_key
    ]
    for rival in rivals:
        storage.delete_profile_item(rival.item_id)
    if winner.status is not ItemStatus.ACTIVE or winner.conflicts_with is not None:
        winner = winner.model_copy(update={"status": ItemStatus.ACTIVE, "conflicts_with": None})
        storage.update_profile_item(winner)
    _rerender(config, storage)
    _logger.info(
        "profile resolve winner=%s rivals=%d name=%s", winner.item_id, len(rivals), winner.name
    )
    return winner, rivals


def rekind_item(
    item_id_prefix: str, new_kind: str, config: Config, storage: Storage
) -> tuple[ProfileItem, ProfileItemKind]:
    """Move one item to a different kind, keeping everything else.

    The correction that previously required delete-and-recapture, which
    destroyed exactly what makes an item worth keeping: its item_id, its
    evidence spans, and its source record. A misfiled item is a *labelling*
    mistake — 'Field of study?' captured as a skill is still true, still
    cited, still the user's own words. Only the heading is wrong.

    Refuses rather than guesses in the three cases where a move would
    quietly damage something: an unknown kind, INTERVIEW as a target (its
    items carry a subtype and persona scope a re-kind cannot synthesise),
    and a name already taken in the destination kind — that last one is a
    real RFC-028 conflict, and silently creating a second row with the
    same (kind, name_key) is how the dedup index stops meaning anything.

    Returns the moved item and the kind it came from.
    """
    item = find_item(item_id_prefix, storage)
    wanted = new_kind.strip().lower()
    valid = [k.value for k in ProfileItemKind if k is not ProfileItemKind.INTERVIEW]
    if wanted not in valid:
        raise IngestError(f"unknown kind {new_kind!r}; expected one of: {', '.join(valid)}.")
    kind = ProfileItemKind(wanted)
    if kind is item.kind:
        raise IngestError(f"{item.item_id[:8]} is already a {kind.value}; nothing to do.")
    rival = next(
        (
            other
            for other in storage.list_profile_items()
            if other.item_id != item.item_id
            and other.kind is kind
            and other.name_key == item.name_key
        ),
        None,
    )
    if rival is not None:
        raise IngestError(
            f"a {kind.value} named {item.name!r} already exists ({rival.item_id[:8]}); "
            "resolve or remove one of them first."
        )
    was = item.kind
    moved = item.model_copy(update={"kind": kind})
    storage.update_profile_item(moved)
    _rerender(config, storage)
    _logger.info(
        "profile rekind id=%s from=%s to=%s name=%s",
        moved.item_id,
        was.value,
        kind.value,
        moved.name,
    )
    return moved, was


def rename_item(
    item_id_prefix: str, new_name: str, config: Config, storage: Storage
) -> tuple[ProfileItem, str]:
    """Give one item a different name, keeping everything else.

    The sibling of rekind_item (#273), for the other half of a mis-capture.
    qa_capture stores the QUESTION as the item's name (RFC-036), so a
    perfectly good achievement can end up called 'Have you shipped an
    AI/LLM product?' — right kind, right evidence, right claim, and a
    prompt where its name should be. Anything reading achievement names
    then reads the interviewer rather than the person (#282).

    name_key is derived from the name, so this changes the dedup key:
    update_profile_item writes the column as well as the payload, which is
    the invariant #273 had to establish for exactly this reason.

    Returns the renamed item and the name it had before.
    """
    item = find_item(item_id_prefix, storage)
    name = new_name.strip()
    if not name:
        raise IngestError("the new name is empty; a profile item must be named.")
    renamed = item.model_copy(update={"name": name})
    if renamed.name_key == item.name_key:
        raise IngestError(f"{item.item_id[:8]} is already named {item.name!r}; nothing to do.")
    rival = next(
        (
            other
            for other in storage.list_profile_items()
            if other.item_id != item.item_id
            and other.kind is item.kind
            and other.name_key == renamed.name_key
        ),
        None,
    )
    if rival is not None:
        raise IngestError(
            f"a {item.kind.value} named {name!r} already exists ({rival.item_id[:8]}); "
            "that is a conflict to resolve, not a rename."
        )
    was = item.name
    storage.update_profile_item(renamed)
    _rerender(config, storage)
    _logger.info("profile rename id=%s from=%r to=%r", renamed.item_id, was, name)
    return renamed, was


def _parse_intensity(raw: str | None) -> SentimentIntensity | None:
    """RFC-050's scale, validated the way the capture path validates it:
    absence is tolerated, a garbled answer never is."""
    text = (raw or "").strip().lower()
    if not text:
        return None
    try:
        return SentimentIntensity(text)
    except ValueError as exc:
        valid = ", ".join(level.value for level in SentimentIntensity)
        raise IngestError(f"unknown intensity {text!r}; use one of: {valid}.") from exc


def _parse_company_reason(raw: str | None) -> CompanyReasonCategory | None:
    """RFC-050's company-reason taxonomy, same tolerance as above."""
    text = (raw or "").strip().lower()
    if not text:
        return None
    try:
        return CompanyReasonCategory(text)
    except ValueError as exc:
        valid = ", ".join(reason.value for reason in CompanyReasonCategory)
        raise IngestError(f"unknown company_reason {text!r}; use one of: {valid}.") from exc


def _amendment_note(
    item: ProfileItem,
    previous_record_id: str,
    previous_why: str,
    revised_at: datetime,
) -> str:
    """The inbox note an amended 'why' resolves against.

    An amended sentence needs somewhere it was actually written, or the
    profile asserts a quote that exists nowhere — the evidence-before-
    assertion invariant every other capture path honours. The original note
    cannot be rewritten (its content hash IS the source record's identity,
    and the earlier wording is the thing the trail exists to keep), so the
    revision gets a note of its own, naming the record it amends and the
    words it replaces.

    `item` is the capture as amended, so the note holds the answer the
    profile now asserts — including the RFC-050/057 fields as they now
    stand, the same three the original capture note records.
    """
    lines = [
        "# Interview capture — amended",
        "",
        f"Item: {item.item_id}",
        f"Amends record: {previous_record_id}",
        f"Amended at: {revised_at.isoformat()}",
        "",
        f"Why: {item.detail}",
        "",
        f"Previously: {previous_why}",
    ]
    if item.value_statement:
        lines += ["", f"What this says they value: {item.value_statement}"]
    if item.intensity is not None:
        lines += ["", f"Intensity: {item.intensity.value}"]
    if item.company_reason is not None:
        lines += ["", f"Company reason: {item.company_reason.value}"]
    return "\n".join(lines) + "\n"


def _amendment_record(
    config: Config,
    storage: Storage,
    item: ProfileItem,
    previous_record_id: str,
    previous_why: str,
    revised_at: datetime,
) -> SourceRecord:
    """Write the amendment note to the inbox and record it as a source.

    The note is written the way every interview capture writes one
    (application/interview.py's own pattern, deliberately: an amendment is
    an interview capture arriving by a third route). It reuses the amended
    record's own `document_key`, so the revision belongs to the SAME
    RFC-028 lineage as the answer it replaces: re-capturing that target in
    conversation later supersedes the amended item cleanly instead of
    colliding with it as a cross-source conflict.
    """
    content = _amendment_note(item, previous_record_id, previous_why, revised_at)
    config.inbox_dir.mkdir(parents=True, exist_ok=True)
    stamp = revised_at.strftime("%Y%m%dT%H%M%S%f")
    path = config.inbox_dir / f"{stamp}-interview-amendment-note.md"
    path.write_text(content, encoding="utf-8")
    data_root = config.data_dir.resolve()
    resolved = path.resolve()
    locator = (
        str(resolved.relative_to(data_root))
        if resolved.is_relative_to(data_root)
        else str(resolved)
    )
    previous = storage.get_source_record(previous_record_id)
    record = SourceRecord(
        source_type=AMENDMENT_SOURCE_TYPE,
        source_locator=locator,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        document_key=previous.document_key if previous is not None else "",
    )
    storage.add_source_record(record)
    return record


def _refuse_document_sourced(item: ProfileItem, storage: Storage) -> None:
    """The line RFC-071 holds: only the author of an answer may revise it.

    Evidence is append-only because a claim must trace to something
    somebody actually wrote. Letting the author of an interview answer
    revise their own answer does not weaken that — they are the source.
    Letting anyone edit a sentence lifted out of a PDF guts it: the profile
    would assert a quote that appears in no document, and nothing
    downstream could tell the difference.

    So the refusal names the document the quote came from and points at the
    two things the person probably wants instead: re-ingesting the fixed
    document (RFC-028 lineage supersedes the old claim by itself), or the
    label-only fixes that were never in question.
    """
    if item.kind is ProfileItemKind.INTERVIEW:
        return
    record = storage.get_source_record(item.evidence[0].source_record_id)
    source = f" quoted from {record.source_locator}" if record is not None else ""
    raise IngestError(
        f"{item.item_id[:8]} is a {item.kind.value} with evidence{source}, not an interview "
        "answer of your own — amending it would leave the profile asserting words that "
        "appear in no document. Fix the document and re-ingest it under the same filename "
        "(the newer version supersedes this claim), or use 'profile rename' for a wrong "
        "name, 'profile rekind' for a wrong heading, 'profile rm' to drop it."
    )


def amend_item(
    item_id_prefix: str,
    config: Config,
    storage: Storage,
    why: str | None = None,
    intensity: str | None = None,
    company_reason: str | None = None,
    value_statement: str | None = None,
) -> tuple[ProfileItem, ProfileItem]:
    """Revise an interview capture's own answer in place (RFC-071, #381).

    The third sibling of rekind_item and rename_item, and the first that
    touches what a claim SAYS rather than how it is filed. Those two keep
    the id, the evidence and the source record because delete-and-recapture
    "would throw away the very lineage worth keeping"; that reasoning
    applies word for word to the 'why', and to RFC-050/RFC-057's
    intensity, company_reason and value_statement, which a capture ingested
    from the interview form (#287) never collected at all — leaving it
    weightless in `my_values` with no way to add a magnitude short of
    destroying the capture, its item id, its source record and the
    (answered in a form) provenance the ingest just stamped on it.

    **Interview captures only.** Amending an achievement whose evidence is
    a line from a resume is a different act — see _refuse_document_sourced.

    **The prior answer is kept, not replaced.** Each amendment appends an
    `ItemRevision` snapshot (the previous why, its evidence spans, and the
    three RFC-050/057 fields as they stood), so the fact that somebody
    changed their mind survives, and the superseded evidence keeps naming
    the source record it rested on. The revision is NOT stored as a second
    evidence span: evidence is a set of independent vouchers, and a person
    restating themselves is not a second voucher (#336's rule).

    **An amended 'why' gets its own source record**, because the amended
    sentence has to be resolvable against something somebody wrote and the
    original note is immutable. It carries the original's `document_key`,
    so the amendment is a new version of the same document rather than a
    rival source.

    Amend SETS, never unsets: an omitted or empty argument leaves that
    field alone, and an answer the person no longer stands behind is a new
    answer, not a blank. `intensity`/`company_reason` are validated exactly
    as `capture_interview_reaction` validates them and no more strictly —
    a field settable at capture and refusable at amend would be a
    difference nobody could predict.

    The caller owns the BP-06 gate: the exact text stored is the exact text
    the person confirmed. See the `profile_manage` MCP docstring, which
    carries the echo-before-save protocol for the connected model.

    Returns (amended item, the item as it was before).
    """
    item = find_item(item_id_prefix, storage)
    _refuse_document_sourced(item, storage)
    if item.status is ItemStatus.SUPERSEDED:
        raise IngestError(
            f"{item.item_id[:8]} was superseded by a later capture of the same target, so "
            "amending it would change nothing anybody reads; amend the active capture "
            "instead (see 'wingman profile list')."
        )

    # An omitted or empty argument means "leave this field alone" — amend
    # sets, never unsets. Whitespace only is a caller who meant to say
    # something and sent nothing, which is worth naming rather than
    # silently treating as absence.
    if why is not None and why != "" and not why.strip():
        raise IngestError("the amended why is empty; a capture has to say why.")
    new_why = (why or "").strip()
    statement = (value_statement or "").strip()
    intensity_value = _parse_intensity(intensity)
    company_reason_value = _parse_company_reason(company_reason)
    if not new_why and not statement and intensity_value is None and company_reason_value is None:
        raise IngestError(
            "nothing to amend; give at least one of why, intensity, company_reason or "
            "value_statement."
        )

    changes: dict[str, object] = {}
    if new_why and new_why != item.detail:
        changes["detail"] = new_why
    if intensity_value is not None and intensity_value is not item.intensity:
        changes["intensity"] = intensity_value
    if company_reason_value is not None and company_reason_value is not item.company_reason:
        changes["company_reason"] = company_reason_value
    if statement and statement != item.value_statement:
        changes["value_statement"] = statement
    if not changes:
        raise IngestError(f"{item.item_id[:8]} already reads exactly that; nothing was amended.")

    revised_at = datetime.now(UTC)
    changes["revisions"] = [
        *item.revisions,
        ItemRevision(
            revised_at=revised_at,
            detail=item.detail,
            evidence=list(item.evidence),
            intensity=item.intensity,
            company_reason=item.company_reason,
            value_statement=item.value_statement,
        ),
    ]
    amended = item.model_copy(update=changes)
    if "detail" in changes:
        # Written from the AMENDED item, so the note holds the answer the
        # profile now asserts; the quote is the amended sentence and it
        # resolves against the note this just wrote.
        record = _amendment_record(
            config, storage, amended, item.evidence[0].source_record_id, item.detail, revised_at
        )
        amended = amended.model_copy(
            update={
                "evidence": [EvidenceSpan(source_record_id=record.record_id, quote=amended.detail)]
            }
        )
    storage.update_profile_item(amended)
    _rerender(config, storage)
    _logger.info(
        "profile amend id=%s fields=%s revisions=%d",
        amended.item_id,
        ",".join(sorted(field for field in changes if field != "revisions")),
        len(amended.revisions),
    )
    return amended, item


def describe_amendment(amended: ProfileItem, before: ProfileItem) -> str:
    """What an amendment actually changed, and what it used to say.

    Echoed back by both the CLI and the MCP tool: "Amended." on its own
    cannot tell somebody whether the field they meant to change is the
    field that changed, and an amendment is the one profile mutation where
    getting the wrong field would silently rewrite evidence. The stored
    text is quoted in full rather than truncated — it is their sentence,
    and a preview that trims it is a preview of something else.
    """
    parts: list[str] = []
    if amended.detail != before.detail:
        parts.append(f'why is now "{amended.detail}" (was "{before.detail}")')
    if amended.intensity is not before.intensity and amended.intensity is not None:
        was = before.intensity.value if before.intensity is not None else "unset"
        parts.append(f"intensity {was} -> {amended.intensity.value}")
    if amended.company_reason is not before.company_reason and amended.company_reason is not None:
        was = before.company_reason.value if before.company_reason is not None else "unset"
        parts.append(f"company_reason {was} -> {amended.company_reason.value}")
    if amended.value_statement != before.value_statement:
        was = f'was "{before.value_statement}"' if before.value_statement else "was unset"
        parts.append(f'value_statement is now "{amended.value_statement}" ({was})')
    return "; ".join(parts)


CORRECTION_SOURCE_TYPE = "evidence_correction"


def _refuse_interview_sourced(item: ProfileItem) -> None:
    """The line `correct` holds, the mirror image of `_refuse_document_sourced`.

    An interview capture already has a path for "that came out wrong" —
    `amend` — and it keeps the RFC-050/RFC-057 scale fields (`intensity`,
    `company_reason`, `value_statement`) `correct` knows nothing about.
    Letting `correct` also touch an INTERVIEW item would give the same
    capture two mutation paths writing two different kinds of note into two
    different lineages, for no reader-visible benefit.
    """
    if item.kind is ProfileItemKind.INTERVIEW:
        raise IngestError(
            f"{item.item_id[:8]} is an interview capture, not a document-sourced item — use "
            "'profile amend' to revise your own answer instead; it also keeps the "
            "intensity/company_reason/value_statement fields 'correct' doesn't touch."
        )


def _find_correctable_span(item: ProfileItem, old_text: str) -> int:
    """The index of the ONE evidence span old_text names, exactly.

    old_text must match a stored quote verbatim — not a substring, not
    modulo whitespace — because the caller has to be pointing at a real,
    specific span, not hoping one contains something like it. Zero matches
    means the caller has the wording wrong (or is looking at a different
    item); more than one means the same quote appears twice on this item,
    which correct refuses rather than guess which one the caller meant.
    """
    matches = [index for index, span in enumerate(item.evidence) if span.quote == old_text]
    if not matches:
        quotes = "; ".join(f'"{span.quote}"' for span in item.evidence)
        raise IngestError(
            f"{item.item_id[:8]}'s evidence does not contain {old_text!r} verbatim; "
            f"current evidence: {quotes}"
        )
    if len(matches) > 1:
        raise IngestError(
            f"{old_text!r} appears in {len(matches)} evidence spans on {item.item_id[:8]}; "
            "correct can only fix one at a time — give the full sentence around it if that "
            "still doesn't disambiguate, or fix the other occurrence in a second call."
        )
    return matches[0]


def _validate_correction(
    item_id_prefix: str, old_text: str, new_text: str, storage: Storage
) -> tuple[ProfileItem, int, str, str]:
    """Everything `preview_correction` and `correct_item` must agree on,
    shared so the diff shown in preview is exactly the diff `correct_item`
    would apply — never two implementations that could drift apart.
    """
    item = find_item(item_id_prefix, storage)
    _refuse_interview_sourced(item)
    if item.status is ItemStatus.SUPERSEDED:
        raise IngestError(
            f"{item.item_id[:8]} was superseded by a later capture of the same target, so "
            "correcting it would change nothing anybody reads; correct the active item "
            "instead (see 'wingman profile list')."
        )
    old = old_text.strip()
    new = new_text.strip()
    if not old:
        raise IngestError("old_text is empty; give the exact evidence text to correct.")
    if not new:
        raise IngestError("new_text is empty; give the corrected wording.")
    index = _find_correctable_span(item, old)
    if new == item.evidence[index].quote:
        raise IngestError(f"{item.item_id[:8]} already reads exactly that; nothing to correct.")
    return item, index, old, new


def preview_correction(item_id_prefix: str, old_text: str, new_text: str, storage: Storage) -> str:
    """The exact diff `correct_item` would apply — read-only, no write.

    The preview half of the two-call confirm gate `profile_manage(action=
    'correct', ..., confirmed=...)` uses, the same shape company_deep_dive,
    people_deep_dive and feature_request already use for an irreversible or
    evidence-altering write: call this (confirmed=false) first, show the
    diff to the user verbatim, and only call `correct_item` (confirmed=true)
    after they explicitly approve it. Raises the same IngestError
    `correct_item` would for an item/text that can't be corrected, so a
    caller never sees a clean preview for a correction that would then fail.
    """
    item, _index, old, new = _validate_correction(item_id_prefix, old_text, new_text, storage)
    return f'{item.kind.value} {item.name!r} ({item.item_id[:8]}):\n  - "{old}"\n  + "{new}"'


def _correction_note(
    item: ProfileItem,
    previous_record_id: str,
    old_text: str,
    new_text: str,
    corrected_at: datetime,
) -> str:
    """The inbox note a corrected evidence span resolves against.

    Exactly `_amendment_note`'s reasoning, transplanted: the corrected quote
    needs somewhere it was actually written, or the profile asserts a quote
    that exists nowhere. The original note cannot be touched — its content
    hash IS the source record's identity — so the correction gets a note of
    its own, naming the record it corrects and the text it replaces.
    """
    lines = [
        "# Evidence correction",
        "",
        f"Item: {item.item_id}",
        f"Corrects record: {previous_record_id}",
        f"Corrected at: {corrected_at.isoformat()}",
        "",
        f"Evidence: {new_text}",
        "",
        f"Previously: {old_text}",
    ]
    return "\n".join(lines) + "\n"


def _correction_record(
    config: Config,
    storage: Storage,
    item: ProfileItem,
    previous_record_id: str,
    old_text: str,
    new_text: str,
    corrected_at: datetime,
) -> SourceRecord:
    """Write the correction note to the inbox and record it as a source.

    Reuses the corrected span's own record's `document_key`, exactly as an
    interview amendment reuses its original's — so the correction belongs
    to the SAME RFC-028 lineage as the claim it fixes: a later re-ingest of
    the (fixed, or unrelated) source document under the same filename
    supersedes the corrected item through ordinary lineage rather than
    landing as a rival source.
    """
    content = _correction_note(item, previous_record_id, old_text, new_text, corrected_at)
    config.inbox_dir.mkdir(parents=True, exist_ok=True)
    stamp = corrected_at.strftime("%Y%m%dT%H%M%S%f")
    path = config.inbox_dir / f"{stamp}-evidence-correction-note.md"
    path.write_text(content, encoding="utf-8")
    data_root = config.data_dir.resolve()
    resolved = path.resolve()
    locator = (
        str(resolved.relative_to(data_root))
        if resolved.is_relative_to(data_root)
        else str(resolved)
    )
    previous = storage.get_source_record(previous_record_id)
    record = SourceRecord(
        source_type=CORRECTION_SOURCE_TYPE,
        source_locator=locator,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        document_key=previous.document_key if previous is not None else "",
    )
    storage.add_source_record(record)
    return record


def correct_item(
    item_id_prefix: str,
    old_text: str,
    new_text: str,
    config: Config,
    storage: Storage,
) -> tuple[ProfileItem, ProfileItem]:
    """Fix a transcription/mishearing error in one evidence span (issue #487).

    `amend`'s mirror image, for exactly the items amend refuses. Their
    evidence is quoted verbatim from a document, and letting anyone edit
    that quote would let the profile assert a claim no document makes —
    amend's refusal protects a real invariant and this tool does not
    reopen it. What it fixes instead is narrower: evidence that WAS
    captured correctly from wherever it came from (a document, or the
    person's own dictated words), but arrived with a transcription error —
    a misheard name, a dropped word — with no path to fix short of
    filesystem access to the workspace inbox, which a hosted tenant does
    not have (RFC-028's "correct the document and re-ingest it" answer
    assumes exactly the access this closes a gap for).

    `old_text` must match one evidence span's stored quote VERBATIM — this
    is deliberately stricter than a substring match, so the caller is
    naming a real, specific span rather than hoping something like it
    exists. Ambiguous (the same quote on two spans) and not-found are both
    refused rather than guessed at (`_find_correctable_span`).

    **The item and its source record are corrected atomically.** A
    correction note is written to the inbox exactly as an interview
    amendment writes one (`_correction_note`/`_correction_record`), the
    corrected span's `source_record_id` is repointed at the new record, and
    both that repointing and the new quote text land in the SAME
    `storage.update_profile_item` call — so nothing observing the item
    between the note write and the item write can see a quote that no
    longer matches its own source record. The corrected span's index is
    the only thing that changes on `evidence`; every other span (a
    multi-quote achievement, say) is untouched.

    **The prior wording is kept, not replaced** — `ItemRevision`,
    `ProfileItem.revisions`, the exact mechanism `amend` already uses, not
    a parallel one: the full evidence list as it stood, the detail, and the
    RFC-050/057 scale fields are all snapshotted before the change. The
    listing then reads '(corrected)' rather than amend's '(revised)' —
    `_revision_note` tells the two apart by the item's own kind, since
    amend and correct never touch the same kind.

    **`detail` is corrected too, when it held the same text.** A
    `qa_capture`/voice-dictated item stores its answer in both `detail` and
    `evidence[0].quote` (the same string, by construction) — correcting
    only the evidence span there would leave the listing (which renders
    `detail`) still showing the mistranscribed name. A document-extracted
    item's `detail` is a separate model-written summary that need not equal
    any quote; it is left alone unless it happens to equal `old_text`
    exactly, so a correction never accidentally rewrites unrelated prose.

    **No character-count or edit-distance gate.** The issue this closes is
    explicit that the protection worth having is the visible diff
    (`preview_correction`) plus the retained revision, not an automated
    length rule — a rule like that would refuse a legitimately short fix
    ("Erica" -> "Arika", 2 characters) exactly as readily as it would wave
    through a long one, and it would teach nothing a human reading the diff
    doesn't already see for free.

    Returns (corrected item, the item as it was before). Callers own the
    confirm gate — see `preview_correction`.
    """
    item, index, old, new = _validate_correction(item_id_prefix, old_text, new_text, storage)
    corrected_at = datetime.now(UTC)
    revisions = [
        *item.revisions,
        ItemRevision(
            revised_at=corrected_at,
            detail=item.detail,
            evidence=list(item.evidence),
            intensity=item.intensity,
            company_reason=item.company_reason,
            value_statement=item.value_statement,
        ),
    ]
    original_record_id = item.evidence[index].source_record_id
    record = _correction_record(config, storage, item, original_record_id, old, new, corrected_at)
    new_evidence = list(item.evidence)
    new_evidence[index] = EvidenceSpan(source_record_id=record.record_id, quote=new)
    changes: dict[str, object] = {"evidence": new_evidence, "revisions": revisions}
    if item.detail == old:
        changes["detail"] = new
    corrected = item.model_copy(update=changes)
    storage.update_profile_item(corrected)
    _rerender(config, storage)
    _logger.info(
        "profile correct id=%s span=%d revisions=%d", corrected.item_id, index, len(revisions)
    )
    return corrected, item


def describe_correction(corrected: ProfileItem, before: ProfileItem) -> str:
    """What a correction actually changed — the same reporting job
    `describe_amendment` does for amend, echoed by both the CLI and the MCP
    tool: 'Corrected.' on its own cannot tell somebody whether the span
    that changed is the one they meant to fix.
    """
    parts: list[str] = []
    for old_span, new_span in zip(before.evidence, corrected.evidence, strict=True):
        if old_span.quote != new_span.quote:
            parts.append(f'evidence is now "{new_span.quote}" (was "{old_span.quote}")')
    if corrected.detail != before.detail:
        parts.append(f'detail is now "{corrected.detail}" (was "{before.detail}")')
    return "; ".join(parts)


def clear_profile(config: Config, storage: Storage) -> int:
    """Delete every profile item and re-render the (now empty) career artifacts.

    Not reversible except via 'wingman restore'; callers own the confirmation.
    Stored job assessments cite item ids that stop existing — re-run assess
    for anything that still matters.
    """
    removed = storage.clear_profile_items()
    _rerender(config, storage)
    _logger.info("profile clear removed=%d", removed)
    return removed


def _scale_tags(item: ProfileItem) -> str:
    """' [mild, product]'-style suffix for an interview nomination's
    sentiment intensity and/or company-reason category (RFC-049, issue
    #240 v1) — empty for every item that doesn't carry either."""
    tags = [item.intensity.value] if item.intensity is not None else []
    if item.company_reason is not None:
        tags.append(item.company_reason.value)
    return f" [{', '.join(tags)}]" if tags else ""


def _value_note(item: ProfileItem) -> str:
    """' — values: "…"' suffix for a nomination that recorded what it tells
    the person they value (RFC-057, issue #343) — empty for every capture
    without one. Quoted and kept whole: it is the person's own words, the
    same status `detail` has, so it is never truncated into a tag."""
    return f' — values: "{item.value_statement}"' if item.value_statement else ""


def _arrival_note(item: ProfileItem) -> str:
    """' (answered in a form)' for a capture ingested from the interview
    form (#287) — empty for everything else.

    Provenance that only exists in the database is provenance nobody
    reads. A form answer is the person's own words, but it was written
    months earlier, offline, without the assistant's follow-up questions,
    and an operator put it here — so somebody reviewing their own profile
    should be able to see which lines those are without querying source
    records. Keyed off `domain.provenance.FORM_EXTRACTOR`, the one value
    every form-arrival capture path sets.
    """
    return " (answered in a form)" if item.extracted_by == FORM_EXTRACTOR else ""


def _revision_note(item: ProfileItem) -> str:
    """' (revised)' for an interview capture whose author amended their own
    answer (RFC-071, issue #381), ' (corrected)' for anything else with a
    fixed transcription error (issue #487) — empty for everything else.

    `amend` and `correct` are mutually exclusive by kind (amend refuses
    every kind but INTERVIEW; correct refuses INTERVIEW), so an item with a
    non-empty `revisions` list got there by exactly one of the two paths,
    and its own kind says which marker is honest — no second field needed
    to remember which tool touched it.

    Sits alongside `(answered in a form)` rather than replacing it: a form
    answer that was later reworded still ARRIVED in a form, and both facts
    are load-bearing for a reader deciding how much weight the sentence
    carries. What is shown is the current wording; that it has a superseded
    predecessor is exactly what this marker says out loud, so nobody has to
    read the database to learn the wording changed.
    """
    if not item.revisions:
        return ""
    return " (revised)" if item.kind is ProfileItemKind.INTERVIEW else " (corrected)"


def render_profile_listing(items: list[ProfileItem]) -> str:
    """The 'wingman profile list' body: active by kind, then conflicts."""
    if not items:
        return "The profile is empty — ingest a resume with 'wingman ingest'."
    active = [item for item in items if item.status is ItemStatus.ACTIVE]
    conflicts = [item for item in items if item.status is ItemStatus.CONFLICT]
    superseded = sum(1 for item in items if item.status is ItemStatus.SUPERSEDED)
    if not active and not conflicts:
        return (
            f"No live profile items ({superseded} superseded by newer document versions) "
            "— ingest a resume with 'wingman ingest'."
        )
    lines: list[str] = []
    for kind in sorted({item.kind for item in active}, key=lambda k: k.value):
        lines.append(f"{kind.value.title()}s:")
        for item in (i for i in active if i.kind is kind):
            detail = f" — {item.detail}" if item.detail else ""
            lines.append(
                f"  {item.item_id[:8]}  {item.name}{detail}{_scale_tags(item)}"
                f"{_value_note(item)}{_arrival_note(item)}{_revision_note(item)}"
            )
    if conflicts:
        lines.append("Conflicts (resolve with 'wingman profile resolve <id>'):")
        for item in conflicts:
            rival = (item.conflicts_with or "?")[:8]
            detail = f" — {item.detail}" if item.detail else ""
            lines.append(
                f"  {item.item_id[:8]}  {item.name}{detail}{_scale_tags(item)}"
                f"{_value_note(item)}{_arrival_note(item)}{_revision_note(item)}"
                f"  (conflicts with {rival})"
            )
    summary = f"{len(active)} active, {len(conflicts)} in conflict"
    if superseded:
        summary += f", {superseded} superseded by newer document versions (hidden)"
    lines.append(summary + ".")
    return "\n".join(lines)
