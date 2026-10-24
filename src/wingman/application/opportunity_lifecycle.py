"""The opportunity lifecycle (RFC-076, #496): an explicit, append-only journal.

Every opportunity starts at ASSESSED. Any other stage is set by exactly one
code path — `update_opportunity` — which runs only on an explicit human act
(a CLI command, an MCP call the user asked for). No stage is ever inferred
from age, from a pack file existing, or from a model judgment: a wrong
automatic transition would make the ledger confidently wrong instead of
honestly uncertain (invariant 9). A test enforces that this module is the
journal's only writer.

The #505 split decides the gate. A stage is a *decision* — the user's own
process, reversible by another update, and journaled — so it is written
without an echo. A note is the user's own words and is saved verbatim, so
the MCP surface writes one only with `confirmed=True`, after the exact text
was shown. A note is never a *claim*: it is not a source record, never
reaches the profile, and no assessment, pack or brief cites it.
"""

from __future__ import annotations

from pydantic import BaseModel

from wingman.application.ingest import IngestError
from wingman.domain.opportunity import Opportunity, OpportunityEvent, OpportunityStatus
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.opportunity_lifecycle")

# An id prefix shorter than this is too likely to match by accident.
_MIN_ID_PREFIX = 4

STAGE_HELP = ", ".join(stage.value for stage in OpportunityStatus)


def parse_stage(value: str) -> OpportunityStatus | None:
    """The stage named by `value`, or None when it is blank."""
    cleaned = value.strip().lower()
    if not cleaned:
        return None
    try:
        return OpportunityStatus(cleaned)
    except ValueError as exc:
        raise IngestError(f"unknown stage {value!r}; use one of: {STAGE_HELP}.") from exc


def resolve_opportunity(ref: str, storage: Storage) -> Opportunity:
    """One opportunity by full id, unique id prefix, or unique title fragment.

    The listing prints an 8-character id per row, so a prefix is the normal
    way to name one; the same role assessed twice has two ids and two
    journals, and a title fragment matching both is refused, not guessed.
    """
    cleaned = ref.strip()
    if not cleaned:
        raise IngestError("no opportunity given; see 'wingman opportunities list'.")
    exact = storage.get_opportunity(cleaned)
    if exact is not None:
        return exact
    opportunities = storage.list_opportunities()
    matches: list[Opportunity] = []
    if len(cleaned) >= _MIN_ID_PREFIX:
        matches = [o for o in opportunities if o.opportunity_id.startswith(cleaned.lower())]
    if not matches:
        needle = " ".join(cleaned.lower().split())
        matches = [o for o in opportunities if needle in " ".join(o.title.lower().split())]
    if not matches:
        raise IngestError(f"no opportunity matching {ref!r}; see 'wingman opportunities list'.")
    if len(matches) > 1:
        listed = "; ".join(f"{o.opportunity_id[:8]} {o.title}" for o in matches[:5])
        raise IngestError(f"{ref!r} matches {len(matches)} opportunities: {listed}. Use the id.")
    return matches[0]


def current_stage(events: list[OpportunityEvent]) -> OpportunityStatus:
    """The latest stage in an oldest-first journal; ASSESSED when none was set."""
    for event in reversed(events):
        if event.stage is not None:
            return event.stage
    return OpportunityStatus.ASSESSED


class UpdateOutcome(BaseModel):
    """What an update did. `saved` is False only for an unconfirmed note,
    in which case nothing at all was written."""

    opportunity: Opportunity
    saved: bool
    event: OpportunityEvent | None = None
    previous_stage: OpportunityStatus
    note: str = ""


def update_opportunity(
    ref: str,
    storage: Storage,
    stage: str = "",
    note: str = "",
    note_confirmed: bool = False,
) -> UpdateOutcome:
    """Append one journal event: a stage change, a note, or both.

    A note is written only when `note_confirmed` is True; otherwise nothing
    is written (not even a stage given alongside it), and the outcome
    carries the exact note so the caller can show it first. Moving to the
    stage the opportunity is already at, with no note, records nothing.
    """
    opportunity = resolve_opportunity(ref, storage)
    new_stage = parse_stage(stage)
    note = " ".join(note.split())
    if new_stage is None and not note:
        raise IngestError(f"give a stage ({STAGE_HELP}) or a note — nothing was recorded.")
    events = storage.list_opportunity_events(opportunity.opportunity_id)
    previous = current_stage(events)
    if new_stage is previous and not note:
        raise IngestError(
            f"{opportunity.title} is already at '{previous.value}' — nothing was recorded."
        )
    if note and not note_confirmed:
        return UpdateOutcome(
            opportunity=opportunity, saved=False, previous_stage=previous, note=note
        )
    event = OpportunityEvent(
        opportunity_id=opportunity.opportunity_id,
        stage=new_stage,
        note=note,
    )
    storage.add_opportunity_event(event)
    _logger.info(
        "opportunity journal opportunity=%s stage=%s note=%s",
        opportunity.opportunity_id,
        new_stage.value if new_stage else "-",
        "yes" if note else "no",
    )
    return UpdateOutcome(
        opportunity=opportunity, saved=True, event=event, previous_stage=previous, note=note
    )


def render_update(outcome: UpdateOutcome) -> str:
    title = f"{outcome.opportunity.title} ({outcome.opportunity.opportunity_id[:8]})"
    if not outcome.saved:
        return (
            f"Nothing saved for {title}. A note is stored verbatim, so show the user "
            f"exactly this text and call again with confirmed=true once they agree:\n"
            f'"{outcome.note}"'
        )
    event = outcome.event
    lines = [f"Recorded for {title}:"]
    if event is not None and event.stage is not None:
        lines.append(f"stage: {outcome.previous_stage.value} -> {event.stage.value}")
    if outcome.note:
        lines.append(f'note: "{outcome.note}"')
    return "\n".join(lines)


def render_journal(opportunity: Opportunity, events: list[OpportunityEvent]) -> str:
    """The full journal, oldest first, opening with the assessment itself."""
    lines = [
        f"Journal — {opportunity.title} ({opportunity.opportunity_id[:8]})",
        f"{opportunity.created_at.date().isoformat()}  assessed",
    ]
    for event in events:
        parts = [event.happened_at.date().isoformat()]
        if event.stage is not None:
            parts.append(f"stage -> {event.stage.value}")
        if event.note:
            parts.append(f'"{event.note}"')
        lines.append("  ".join(parts))
    lines.append(f"Current stage: {current_stage(events).value}")
    return "\n".join(lines)
