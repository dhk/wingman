"""Relationship objectives (RFC-037): goal, thesis, next move — per person.

A week of angles conversations (who is this person, why engage, what's the
play) produces exactly this triple, and it used to evaporate into chat while
the mechanical outputs (feeds, briefs) persisted. This module gives it a
home: one objective per watched person, seeded and revised by an interview
conversation whose protocol lives on the MCP tool's docstring
(RFC-025/030/031/035/036 convention — the connected client interviews,
drafts, shows, and saves only after the user confirms the wording). Saving
always replaces the current triple, the same "rebuilt, not versioned"
posture as a POV card: the objective is a living judgment about a
relationship, not a lineage of superseded claims.

The objective doc is the source of truth for the relationship (RFC-035
style): any model proposal about the person must cite it plus evidence —
their writing, logged interactions — never a free-floating judgment.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel

from wingman.application.ingest import IngestError
from wingman.application.people import match_people
from wingman.domain.person import Person
from wingman.domain.relationship import RelationshipLogEntry, RelationshipObjective
from wingman.domain.source_record import SourceRecord
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.relationship")

LOG_SOURCE_TYPE = "relationship_log"
# How many recent log entries surface in a brief's deterministic footer.
_BRIEF_LOG_LIMIT = 3
# Past this many days without a revision, the overnight digest nudges a
# review (RFC-037 pt 4) — the RFC-035 review loop, re-aimed at
# relationships. Longer than the tickler's cadence (focus.TICKLER_STALE_DAYS,
# 7 days): the tickler nudges acting on the next move, this asks whether
# the objective itself is still the right one.
REVIEW_EVERY_DAYS = 45

# The interview loop: one question set, used for seeding AND for revision, so
# the objective never fossilizes. Every surface — the MCP packet, the CLI —
# reads this one list.
INTERVIEW_AREAS: list[tuple[str, str]] = [
    (
        "Goal",
        (
            "Why are you investing in this relationship? What would make it "
            "worth the time, concretely?"
        ),
    ),
    (
        "Thesis",
        (
            "What do you believe about the path from here — how does this "
            "relationship actually develop toward the goal?"
        ),
    ),
    (
        "Next move",
        (
            "What's the next intended action, concrete enough to act on? "
            "(a message, a topic to raise, an event to wait for)"
        ),
    ),
]


def _resolve_person(name: str, storage: Storage) -> Person:
    matches = match_people(storage, name)
    if not matches:
        raise IngestError(f"no person matching {name!r}; see 'wingman people list'.")
    if len(matches) > 1:
        names = ", ".join(person.name for person in matches[:5])
        raise IngestError(f"{name!r} matches multiple people: {names}. Use the full name.")
    return matches[0]


def load_objective(name: str, storage: Storage) -> tuple[Person, RelationshipObjective | None]:
    """The person and their current objective (None if never seeded)."""
    person = _resolve_person(name, storage)
    return person, storage.get_objective(person.person_id)


def save_objective(
    name: str,
    goal: str,
    thesis: str,
    next_move: str,
    storage: Storage,
) -> RelationshipObjective:
    """Replace the objective for a person — the user's own confirmed words."""
    person = _resolve_person(name, storage)
    goal = goal.strip()
    thesis = thesis.strip()
    next_move = next_move.strip()
    if not goal or not thesis or not next_move:
        raise IngestError("goal, thesis, and next_move are all required — nothing was saved.")
    existing = storage.get_objective(person.person_id)
    now = datetime.now(UTC)
    objective = RelationshipObjective(
        objective_id=existing.objective_id if existing else str(uuid4()),
        person_id=person.person_id,
        goal=goal,
        thesis=thesis,
        next_move=next_move,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    storage.save_objective(objective)
    _logger.info("objective %s person=%s", "updated" if existing else "saved", person.name)
    return objective


def render_objective(person: Person, objective: RelationshipObjective) -> str:
    age_days = (datetime.now(UTC) - objective.updated_at).days
    return (
        f"Relationship objective — {person.name} (updated {age_days} day(s) ago)\n"
        f"Goal: {objective.goal}\n"
        f"Thesis: {objective.thesis}\n"
        f"Next move: {objective.next_move}"
    )


def render_interview(name: str, storage: Storage) -> str:
    """The interview packet: current triple (if any) plus the three areas —
    for either seeding a first objective or revising an existing one."""
    person = _resolve_person(name, storage)
    objective = storage.get_objective(person.person_id)
    lines: list[str] = []
    if objective is None:
        lines.append(
            f"Relationship objective interview — SEEDING ({person.name}, no objective yet)."
        )
        lines.append(
            "Walk the three areas below with the user, one at a time; capture "
            "answers in their words."
        )
    else:
        age_days = (datetime.now(UTC) - objective.updated_at).days
        lines.append(
            f"Relationship objective interview — REVISE ({person.name}, last set {age_days} day(s) ago)."
        )
        lines.append("Current objective:")
        lines.append("---")
        lines.append(f"Goal: {objective.goal}")
        lines.append(f"Thesis: {objective.thesis}")
        lines.append(f"Next move: {objective.next_move}")
        lines.append("---")
        lines.append(
            "Walk the three areas below one at a time: read what the current "
            "objective says about the area, ask whether it strengthened, stalled, "
            "or the thesis was wrong, and offer Keep / Update."
        )
    for number, (area, prompt) in enumerate(INTERVIEW_AREAS, start=1):
        lines.append(f"{number}. {area} — {prompt}")
    lines.append(
        "When all three areas are done: draft the full revised triple, show it, "
        "iterate until the user confirms the wording, then call "
        "relationship_objective(action='save'). Save nothing without confirmation."
    )
    return "\n".join(lines)


class LogReport(BaseModel):
    person: str
    entry: RelationshipLogEntry
    source_path: str


def log_interaction(name: str, note: str, config: Config, storage: Storage) -> LogReport:
    """Record what actually happened with a person — the qa_capture way
    (RFC-036): deterministic, person-attributed, the user's own words as
    the evidence quote, zero model calls. The raw material a future brief
    or objective revision can cite."""
    person = _resolve_person(name, storage)
    note = " ".join(note.split())
    if not note:
        raise IngestError("the note is empty — nothing was logged.")
    content = f"# Relationship log\n\nPerson: {person.name}\n\n{note}\n"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    record = storage.get_source_record_by_hash(content_hash)
    if record is None:
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
            content_hash=content_hash,
            document_key=f"relationship-log:{person.person_id}:{stamp}",
        )
        storage.add_source_record(record)
    entry = RelationshipLogEntry(
        person_id=person.person_id,
        source_record_id=record.record_id,
        note=note,
    )
    storage.add_log_entry(entry)
    _logger.info("relationship logged person=%s", person.name)
    return LogReport(person=person.name, entry=entry, source_path=record.source_locator)


def list_log(name: str, storage: Storage) -> tuple[Person, list[RelationshipLogEntry]]:
    """A person and their interaction log, oldest first."""
    person = _resolve_person(name, storage)
    return person, storage.list_log_entries(person.person_id)


def render_log_entry(entry: RelationshipLogEntry) -> str:
    when = entry.happened_at.date().isoformat()
    return f"{when}  {entry.note}"


def render_log(person: Person, entries: list[RelationshipLogEntry]) -> str:
    if not entries:
        return (
            f"No interactions logged for {person.name} yet — "
            f'wingman log "{person.name}" "<what happened>"'
        )
    lines = [f"Interaction log — {person.name}"]
    lines.extend(render_log_entry(entry) for entry in entries)
    lines.append(f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}.")
    return "\n".join(lines)


def render_relationship_context(person: Person, storage: Storage) -> str:
    """A deterministic footer for briefs and proposals: the objective (if
    any) plus the most recent logged interactions — the evidence a model
    proposal about this person must cite (RFC-037), never generated text."""
    objective = storage.get_objective(person.person_id)
    entries = storage.list_log_entries(person.person_id)
    if objective is None and not entries:
        return ""
    lines = [f"--- Relationship context — {person.name} (RFC-037, cite this, don't invent) ---"]
    if objective is not None:
        lines.append(f"Goal: {objective.goal}")
        lines.append(f"Thesis: {objective.thesis}")
        lines.append(f"Next move: {objective.next_move}")
    if entries:
        lines.append("Recent interactions:")
        lines.extend(f"  {render_log_entry(entry)}" for entry in entries[-_BRIEF_LOG_LIMIT:])
    return "\n".join(lines)
