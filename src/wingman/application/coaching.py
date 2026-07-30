"""Coaching mode (docs/COACHING-MODE-DESIGN.md): the coach runs wingman's
existing analyses on behalf of people they coach, from their own instance.
Coach-mediated only — the coach always drives every tool call; there is no
separate persona-facing login or access token.

`set_active_persona` finds-or-creates a Persona by name (case/whitespace-
insensitive) and persists it as the active one; every persona-aware tool
call defaults to whatever's active unless given an explicit override. The
active pointer persists until explicitly cleared — no expiry — which is
only safe because `render_acting_as` is meant to be folded into every
persona-scoped tool's response, so which persona is active is always
visible, never something the coach has to separately check.
"""

from __future__ import annotations

from wingman.application.ingest import IngestError
from wingman.domain.persona import Persona
from wingman.infrastructure.coach_state import (
    clear_active_persona,
    read_active_persona_id,
    write_active_persona_id,
)
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.coaching")


def find_or_create_persona(name: str, storage: Storage, notes: str = "") -> Persona:
    """The named persona, creating one if this name hasn't been coached
    before. Matching is case/whitespace-insensitive so "Mike" and "mike "
    resolve to the same persona rather than silently forking one into two."""
    name = name.strip()
    if not name:
        raise IngestError("a persona needs a name — nothing was set.")
    existing = storage.find_persona_by_name(name)
    if existing is not None:
        return existing
    persona = Persona(name=name, notes=notes.strip())
    storage.add_persona(persona)
    _logger.info("persona_created name=%r persona_id=%s", name, persona.persona_id)
    return persona


def set_active_persona(name: str, storage: Storage, config: Config) -> Persona:
    """Act as coach for `name` — finds-or-creates the persona and makes it
    the default scope for every persona-aware tool call until cleared."""
    persona = find_or_create_persona(name, storage)
    write_active_persona_id(config, persona.persona_id)
    _logger.info("active_persona_set persona_id=%s name=%r", persona.persona_id, persona.name)
    return persona


def clear_active_persona_and_report(config: Config) -> None:
    clear_active_persona(config)
    _logger.info("active_persona_cleared")


def get_active_persona(storage: Storage, config: Config) -> Persona | None:
    """The currently active persona, or None for "the coach's own work" —
    resolved fresh from storage every call, never cached, so a persona
    deleted or renamed elsewhere is reflected immediately rather than
    stale. A dangling pointer (the id no longer resolves) is treated the
    same as never having been set, not an error — coaching mode degrades
    to the coach's own work rather than failing a call outright."""
    persona_id = read_active_persona_id(config)
    if persona_id is None:
        return None
    return storage.get_persona(persona_id)


def resolve_persona(override_name: str, storage: Storage, config: Config) -> Persona | None:
    """The persona a persona-aware tool call should use: an explicit
    override name if given (finding-or-creating it, for a one-off
    cross-persona action that shouldn't disturb the active pointer), else
    whatever's currently active, else None (the coach's own work)."""
    if override_name.strip():
        return find_or_create_persona(override_name, storage)
    return get_active_persona(storage, config)


def render_acting_as(persona: Persona | None) -> str:
    """The visible indicator every persona-aware tool response folds in —
    load-bearing, not decorative: this is what keeps an indefinitely
    persisted active-persona pointer from becoming a silent trap."""
    if persona is None:
        return "Acting as: yourself."
    return f"Acting as: coach for {persona.name}."
