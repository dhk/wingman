"""Relationships beyond people (RFC-085, #647): log, link, aim and set objectives
for companies, opportunities, initiatives and content pieces.

Each kind keeps the store it already had. A person's log entry is still a
`relationship_log` row and its objective still a `relationship_objectives`
row (RFC-037), so nothing migrates and every existing caller — briefs,
completeness, merges, transcript filing — reads what it always read. An
opportunity logs into its journal (RFC-076), never a second history.
Companies, initiatives and content log into `subject_log`. On top of all
three, `log_links` lets one entry appear in several timelines, and
`content_targets` records which people or companies a content piece is
aimed at.

The evidence contract is unchanged for every kind: the note is stored
verbatim, deterministic, zero model calls, with an RFC-073 evidence tier;
the echo-before-save protocol lives on the MCP tools' docstrings exactly as
it does for people. Every link is resolved before anything is written, so
a call naming one unknown subject writes nothing at all.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel

from wingman.application.ingest import IngestError
from wingman.application.opportunity_lifecycle import resolve_opportunity, update_opportunity
from wingman.application.relationship import (
    LOG_SOURCE_TYPE,
    _resolve_person,
    load_objective,
    log_interaction,
    save_objective,
)
from wingman.application.similarity import company_key
from wingman.domain.relationship import EvidenceTier
from wingman.domain.source_record import SourceRecord
from wingman.domain.subject import (
    RECORD_KINDS,
    Subject,
    SubjectKind,
    SubjectLogEntry,
    SubjectObjective,
    SubjectRef,
)
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import DuplicateRecordError, Storage

_logger = get_logger("application.subjects")

KIND_HELP = ", ".join(kind.value for kind in SubjectKind)
# A content piece is aimed at the people and organizations it should reach.
TARGET_KINDS = (SubjectKind.PERSON, SubjectKind.COMPANY)


def parse_kind(value: str) -> SubjectKind:
    cleaned = value.strip().lower() or SubjectKind.PERSON.value
    try:
        return SubjectKind(cleaned)
    except ValueError as exc:
        raise IngestError(f"unknown subject kind {value!r}; use one of: {KIND_HELP}.") from exc


def resolve_subject(kind: SubjectKind, ref: str, storage: Storage) -> SubjectRef:
    """The subject `ref` names within `kind`, or IngestError naming the fix."""
    cleaned = " ".join(ref.split())
    if not cleaned:
        raise IngestError(f"no {kind.value} named; nothing was resolved.")
    if kind is SubjectKind.PERSON:
        person = _resolve_person(cleaned, storage)
        return SubjectRef(kind=kind, subject_id=person.person_id, label=person.name)
    if kind is SubjectKind.COMPANY:
        key = company_key(cleaned)
        if not key:
            raise IngestError(f"{ref!r} is not a usable company name.")
        return SubjectRef(kind=kind, subject_id=key, label=cleaned)
    if kind is SubjectKind.OPPORTUNITY:
        opportunity = resolve_opportunity(cleaned, storage)
        return SubjectRef(kind=kind, subject_id=opportunity.opportunity_id, label=opportunity.title)
    subject = storage.find_subject(kind, cleaned.lower())
    if subject is None:
        raise IngestError(
            f"no {kind.value} named {cleaned!r}; add it first "
            f"('wingman subjects add --kind {kind.value}' or the subjects tool)."
        )
    return SubjectRef(kind=kind, subject_id=subject.subject_id, label=subject.name)


def resolve_link(spec: str, storage: Storage) -> SubjectRef:
    """A 'kind:name' link ('company:Sage', 'initiative:AI practice')."""
    kind_text, sep, name = spec.partition(":")
    if not sep:
        raise IngestError(f"link {spec!r} needs the form kind:name (kinds: {KIND_HELP}).")
    return resolve_subject(parse_kind(kind_text), name, storage)


def _resolve_links(primary: SubjectRef, specs: list[str], storage: Storage) -> list[SubjectRef]:
    links: list[SubjectRef] = []
    seen = {primary.key()}
    for spec in specs:
        if not spec.strip():
            continue
        ref = resolve_link(spec, storage)
        if ref.key() not in seen:
            seen.add(ref.key())
            links.append(ref)
    return links


def add_subject(
    kind: SubjectKind, name: str, storage: Storage, description: str = "", url: str = ""
) -> Subject:
    if kind not in RECORD_KINDS:
        raise IngestError(
            f"only {' and '.join(k.value for k in RECORD_KINDS)} are added here; "
            f"a {kind.value} is referenced where it already lives."
        )
    name = " ".join(name.split())
    if not name:
        raise IngestError(f"the {kind.value} needs a name — nothing was added.")
    subject = Subject(kind=kind, name=name, description=description.strip(), url=url.strip())
    try:
        storage.add_subject(subject)
    except DuplicateRecordError as exc:
        raise IngestError(f"a {kind.value} named {name!r} already exists.") from exc
    _logger.info("subject added kind=%s", kind.value)
    return subject


class SubjectLogReport(BaseModel):
    subject: SubjectRef
    entry_id: str
    note: str
    links: list[SubjectRef]
    source_path: str = ""


def _write_subject_entry(
    subject: SubjectRef,
    note: str,
    config: Config,
    storage: Storage,
    evidence_tier: EvidenceTier,
    source_url: str,
) -> tuple[SubjectLogEntry, str]:
    """The RFC-037 recipe for a non-person subject: its own inbox file and
    source record, then the entry. Never reuses a prior record — the log is
    a timeline, so the same short note twice is two events."""
    tier_line = f"Evidence: {evidence_tier.value}\n"
    source_line = f"Source: {source_url}\n" if source_url else ""
    content = (
        f"# Relationship log\n\nSubject: {subject.kind.value} {subject.label}\n"
        f"{tier_line}{source_line}\n{note}\n"
    )
    config.inbox_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    path = config.inbox_dir / f"{stamp}-relationship-log.md"
    path.write_text(content, encoding="utf-8")
    data_root = config.data_dir.resolve()
    resolved = path.resolve()
    record = SourceRecord(
        source_type=LOG_SOURCE_TYPE,
        source_locator=str(resolved.relative_to(data_root))
        if resolved.is_relative_to(data_root)
        else str(resolved),
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        document_key=f"relationship-log:{subject.kind.value}:{subject.subject_id}:{stamp}",
    )
    storage.add_source_record(record)
    entry = SubjectLogEntry(
        subject=subject,
        source_record_id=record.record_id,
        note=note,
        evidence_tier=evidence_tier,
        source_url=source_url,
    )
    storage.add_subject_log_entry(entry)
    return entry, record.source_locator


def log_for_subject(
    kind: SubjectKind,
    ref: str,
    note: str,
    config: Config,
    storage: Storage,
    evidence_tier: EvidenceTier = EvidenceTier.OBSERVED,
    source_url: str = "",
    links: list[str] | None = None,
) -> SubjectLogReport:
    """Log what happened against one subject, linked to any others.

    Resolution happens before any write: an unknown subject or link means
    nothing is stored. A person entry is the RFC-037 `log_interaction`,
    unchanged. An opportunity entry is a note-only event in its journal
    (RFC-076) and is always the user's own words, so 'endorsed' is refused.
    """
    primary = resolve_subject(kind, ref, storage)
    linked = _resolve_links(primary, links or [], storage)
    note = " ".join(note.split())
    if not note:
        raise IngestError("the note is empty — nothing was logged.")
    source_url = source_url.strip()
    if kind is SubjectKind.PERSON:
        report = log_interaction(
            primary.label, note, config, storage, evidence_tier=evidence_tier, source_url=source_url
        )
        entry_id, source_path = report.entry.entry_id, report.source_path
    elif kind is SubjectKind.OPPORTUNITY:
        if evidence_tier is not EvidenceTier.OBSERVED:
            raise IngestError(
                "an opportunity's journal holds the user's own words only; log a "
                "synthesis against the person or company it came from."
            )
        outcome = update_opportunity(primary.subject_id, storage, note=note, note_confirmed=True)
        if outcome.event is None:  # unreachable: a confirmed note is always written
            raise IngestError("the journal event was not written — nothing was logged.")
        entry_id, source_path = outcome.event.event_id, ""
    else:
        entry, source_path = _write_subject_entry(
            primary, note, config, storage, evidence_tier, source_url
        )
        entry_id = entry.entry_id
    if linked:
        storage.add_log_links(entry_id, linked)
    _logger.info("subject logged kind=%s links=%d", kind.value, len(linked))
    return SubjectLogReport(
        subject=primary, entry_id=entry_id, note=note, links=linked, source_path=source_path
    )


class TimelineEntry(BaseModel):
    entry_id: str
    happened_at: datetime
    text: str
    logged_against: SubjectRef
    links: list[SubjectRef]
    tier: EvidenceTier = EvidenceTier.OBSERVED


def _find_entry(entry_id: str, storage: Storage) -> TimelineEntry | None:
    """Look an entry up in whichever of the three stores holds it."""
    links = storage.list_links_from(entry_id)
    person_entry = storage.get_log_entry(entry_id)
    if person_entry is not None:
        person = storage.get_person(person_entry.person_id)
        label = person.name if person else person_entry.person_id
        return TimelineEntry(
            entry_id=entry_id,
            happened_at=person_entry.happened_at,
            text=person_entry.note,
            logged_against=SubjectRef(
                kind=SubjectKind.PERSON, subject_id=person_entry.person_id, label=label
            ),
            links=links,
            tier=person_entry.evidence_tier,
        )
    subject_entry = storage.get_subject_log_entry(entry_id)
    if subject_entry is not None:
        return TimelineEntry(
            entry_id=entry_id,
            happened_at=subject_entry.happened_at,
            text=subject_entry.note,
            logged_against=subject_entry.subject,
            links=links,
            tier=subject_entry.evidence_tier,
        )
    event = storage.get_opportunity_event(entry_id)
    if event is not None:
        opportunity = storage.get_opportunity(event.opportunity_id)
        return TimelineEntry(
            entry_id=entry_id,
            happened_at=event.happened_at,
            text=_event_text(event.stage.value if event.stage else "", event.note),
            logged_against=SubjectRef(
                kind=SubjectKind.OPPORTUNITY,
                subject_id=event.opportunity_id,
                label=opportunity.title if opportunity else event.opportunity_id,
            ),
            links=links,
        )
    return None


def _event_text(stage: str, note: str) -> str:
    parts = [f"stage -> {stage}"] if stage else []
    if note:
        parts.append(note)
    return "; ".join(parts)


def timeline(subject: SubjectRef, storage: Storage) -> list[TimelineEntry]:
    """Everything logged against this subject plus everything linked to it,
    oldest first — one event, however many timelines it belongs to."""
    if subject.kind is SubjectKind.PERSON:
        own_ids = [entry.entry_id for entry in storage.list_log_entries(subject.subject_id)]
    elif subject.kind is SubjectKind.OPPORTUNITY:
        own_ids = [event.event_id for event in storage.list_opportunity_events(subject.subject_id)]
    else:
        own_ids = [entry.entry_id for entry in storage.list_subject_log_entries(subject)]
    ids = list(dict.fromkeys(own_ids + storage.list_entries_linked_to(subject)))
    entries = [found for entry_id in ids if (found := _find_entry(entry_id, storage)) is not None]
    return sorted(entries, key=lambda entry: (entry.happened_at, entry.entry_id))


def render_timeline(subject: SubjectRef, entries: list[TimelineEntry]) -> str:
    if not entries:
        return f"Nothing logged for {subject.kind.value} {subject.label} yet."
    lines = [f"Timeline — {subject.kind.value} {subject.label}"]
    for entry in entries:
        tag = "" if entry.tier is EvidenceTier.OBSERVED else " [endorsed]"
        via = (
            ""
            if entry.logged_against.key() == subject.key()
            else f"  (logged against {entry.logged_against.kind.value} {entry.logged_against.label})"
        )
        others = [ref for ref in entry.links if ref.key() != subject.key()]
        if entry.logged_against.key() != subject.key():
            others = [ref for ref in others if ref.key() != entry.logged_against.key()]
        also = (
            f"  [also: {', '.join(f'{r.kind.value} {r.label}' for r in others)}]" if others else ""
        )
        lines.append(f"{entry.happened_at.date().isoformat()}{tag}  {entry.text}{via}{also}")
    lines.append(f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}.")
    return "\n".join(lines)


def render_linked_for_person(subject: SubjectRef, storage: Storage) -> str:
    """Entries logged elsewhere but linked to this person, for the end of
    their RFC-037 log listing; empty when there are none."""
    own = {entry.entry_id for entry in storage.list_log_entries(subject.subject_id)}
    linked = [
        found
        for entry_id in storage.list_entries_linked_to(subject)
        if entry_id not in own and (found := _find_entry(entry_id, storage)) is not None
    ]
    if not linked:
        return ""
    lines = ["Linked from other timelines:"]
    for entry in sorted(linked, key=lambda e: e.happened_at):
        lines.append(
            f"{entry.happened_at.date().isoformat()}  {entry.text}"
            f"  (logged against {entry.logged_against.kind.value} {entry.logged_against.label})"
        )
    return "\n".join(lines)


# --- Objectives -------------------------------------------------------------


class ObjectiveView(BaseModel):
    subject: SubjectRef
    goal: str
    thesis: str
    next_move: str
    updated_at: datetime


def load_subject_objective(subject: SubjectRef, storage: Storage) -> ObjectiveView | None:
    if subject.kind is SubjectKind.PERSON:
        _, person_objective = load_objective(subject.label, storage)
        if person_objective is None:
            return None
        return ObjectiveView(
            subject=subject,
            goal=person_objective.goal,
            thesis=person_objective.thesis,
            next_move=person_objective.next_move,
            updated_at=person_objective.updated_at,
        )
    objective = storage.get_subject_objective(subject)
    if objective is None:
        return None
    return ObjectiveView(
        subject=subject,
        goal=objective.goal,
        thesis=objective.thesis,
        next_move=objective.next_move,
        updated_at=objective.updated_at,
    )


def save_subject_objective(
    subject: SubjectRef, goal: str, thesis: str, next_move: str, storage: Storage
) -> ObjectiveView:
    """Replace the triple — the user's own confirmed words. A person's goes
    through RFC-037's save_objective, unchanged."""
    if subject.kind is SubjectKind.PERSON:
        saved = save_objective(subject.label, goal, thesis, next_move, storage)
        return ObjectiveView(
            subject=subject,
            goal=saved.goal,
            thesis=saved.thesis,
            next_move=saved.next_move,
            updated_at=saved.updated_at,
        )
    goal, thesis, next_move = goal.strip(), thesis.strip(), next_move.strip()
    if not goal or not thesis or not next_move:
        raise IngestError("goal, thesis, and next_move are all required — nothing was saved.")
    existing = storage.get_subject_objective(subject)
    now = datetime.now(UTC)
    objective = SubjectObjective(
        objective_id=existing.objective_id if existing else str(uuid4()),
        subject=subject,
        goal=goal,
        thesis=thesis,
        next_move=next_move,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    storage.save_subject_objective(objective)
    return ObjectiveView(
        subject=subject, goal=goal, thesis=thesis, next_move=next_move, updated_at=now
    )


def render_subject_objective(view: ObjectiveView) -> str:
    age_days = (datetime.now(UTC) - view.updated_at).days
    return (
        f"Objective — {view.subject.kind.value} {view.subject.label} "
        f"(updated {age_days} day(s) ago)\n"
        f"Goal: {view.goal}\nThesis: {view.thesis}\nNext move: {view.next_move}"
    )


def render_subject_interview(subject: SubjectRef, storage: Storage) -> str:
    """The RFC-037 interview packet, worded for a subject that is not a person."""
    current = load_subject_objective(subject, storage)
    what = f"{subject.kind.value} {subject.label}"
    if current:
        lines = [f"Objective interview — REVISE ({what})."]
    else:
        lines = [f"Objective interview — SEEDING ({what}, no objective yet)."]
    if current:
        lines += [
            "Current objective:",
            "---",
            f"Goal: {current.goal}",
            f"Thesis: {current.thesis}",
            f"Next move: {current.next_move}",
            "---",
            (
                "Walk the three areas one at a time: read what the current objective "
                "says, ask whether it strengthened, stalled, or the thesis was wrong, "
                "and offer Keep / Update."
            ),
        ]
    lines += [
        f"1. Goal — What do you want from this {subject.kind.value}, concretely?",
        "2. Thesis — What do you believe about the path from here?",
        "3. Next move — What's the next intended action, concrete enough to act on?",
        (
            "When all three areas are done: draft the triple, show it, iterate until "
            "the user confirms the wording, then save. Save nothing without confirmation."
        ),
    ]
    return "\n".join(lines)


# --- Content targets ----------------------------------------------------------


def _content(ref: str, storage: Storage) -> SubjectRef:
    return resolve_subject(SubjectKind.CONTENT, ref, storage)


def aim_content(
    content: str, target_spec: str, storage: Storage
) -> tuple[SubjectRef, SubjectRef, bool]:
    """Aim a content piece at a person or company ('company:Sage')."""
    piece = _content(content, storage)
    target = resolve_link(target_spec, storage)
    if target.kind not in TARGET_KINDS:
        raise IngestError(f"content is aimed at a person or a company, not a {target.kind.value}.")
    return piece, target, storage.add_content_target(piece.subject_id, target)


def unaim_content(
    content: str, target_spec: str, storage: Storage
) -> tuple[SubjectRef, SubjectRef, bool]:
    piece = _content(content, storage)
    target = resolve_link(target_spec, storage)
    return piece, target, storage.remove_content_target(piece.subject_id, target)


def content_aimed_at(subject: SubjectRef, storage: Storage) -> list[SubjectRef]:
    pieces = []
    for content_id in storage.list_content_aimed_at(subject):
        piece = storage.get_subject(content_id)
        if piece is not None:
            pieces.append(
                SubjectRef(kind=SubjectKind.CONTENT, subject_id=content_id, label=piece.name)
            )
    return pieces


def render_subject(subject: SubjectRef, storage: Storage) -> str:
    """Everything known about one subject: the record (for an initiative or
    content piece), its objective, what it is aimed at or is the target of,
    and its timeline."""
    lines: list[str] = []
    if subject.kind in RECORD_KINDS:
        record = storage.get_subject(subject.subject_id)
        if record is not None:
            lines.append(f"{record.kind.value.capitalize()}: {record.name}")
            if record.description:
                lines.append(record.description)
            if record.url:
                lines.append(record.url)
    objective = load_subject_objective(subject, storage)
    lines.append(render_subject_objective(objective) if objective else "No objective set yet.")
    if subject.kind is SubjectKind.CONTENT:
        targets = storage.list_content_targets(subject.subject_id)
        aimed = ", ".join(f"{t.kind.value} {t.label}" for t in targets) or "nobody yet"
        lines.append(f"Aimed at: {aimed}")
    elif subject.kind in TARGET_KINDS:
        pieces = content_aimed_at(subject, storage)
        if pieces:
            lines.append(f"Content aimed here: {', '.join(p.label for p in pieces)}")
    lines.append(render_timeline(subject, timeline(subject, storage)))
    return "\n".join(lines)


def render_subject_list(subjects: list[Subject]) -> str:
    if not subjects:
        return (
            "No initiatives or content pieces yet — add one with "
            "subjects(action='add', kind='initiative', name=...)."
        )
    lines = [
        f"{subject.kind.value}: {subject.name}"
        + (f" — {subject.description}" if subject.description else "")
        for subject in subjects
    ]
    lines.append(f"{len(subjects)} subject{'s' if len(subjects) != 1 else ''}.")
    return "\n".join(lines)
