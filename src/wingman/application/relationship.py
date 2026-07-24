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

from datetime import UTC, datetime
from uuid import uuid4

from wingman.application.ingest import IngestError
from wingman.application.people import match_people
from wingman.domain.person import Person
from wingman.domain.relationship import RelationshipObjective
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.relationship")

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
            "objective says about the area, ask what changed, offer Keep / Update."
        )
    for number, (area, prompt) in enumerate(INTERVIEW_AREAS, start=1):
        lines.append(f"{number}. {area} — {prompt}")
    lines.append(
        "When all three areas are done: draft the full revised triple, show it, "
        "iterate until the user confirms the wording, then call "
        "relationship_objective(action='save'). Save nothing without confirmation."
    )
    return "\n".join(lines)
