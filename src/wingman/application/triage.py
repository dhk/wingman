"""Digest action triage (RFC-031): your verdicts decide what stops rolling over.

The morning action list was accumulating "uninteresting" items day after
day because nothing remembered what the user had already decided to
ignore. Wingman does not guess what is uninteresting — the user says so,
one verdict at a time, and the verdicts persist: **mute** hides an action
key forever, **snooze** hides it until a date. Every digest action
carries its key, so a verdict given in conversation (the connected client
walking the list with AskUserQuestion) or on the CLI applies to all
future digests. Expired snoozes are pruned on read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from wingman.application.ingest import IngestError
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

if TYPE_CHECKING:
    from wingman.application.focus import ActionItem

_logger = get_logger("application.triage")

MUTE = "mute"
SNOOZE = "snooze"


def mute_action(key: str, storage: Storage) -> None:
    if not key.strip():
        raise IngestError("action key is empty; keys appear under each digest action.")
    storage.set_action_verdict(key.strip(), MUTE)
    _logger.info("action muted key=%s", key.strip())


def snooze_action(key: str, storage: Storage, days: int = 7) -> str:
    if not key.strip():
        raise IngestError("action key is empty; keys appear under each digest action.")
    if days < 1:
        raise IngestError("snooze needs at least 1 day.")
    until = (datetime.now(UTC) + timedelta(days=days)).date().isoformat()
    storage.set_action_verdict(key.strip(), SNOOZE, until=until)
    _logger.info("action snoozed key=%s until=%s", key.strip(), until)
    return until


def unmute_action(key: str, storage: Storage) -> bool:
    return storage.clear_action_verdict(key.strip())


def active_suppressions(storage: Storage) -> dict[str, str]:
    """action_key -> human reason, with expired snoozes pruned as they're seen."""
    today = datetime.now(UTC).date().isoformat()
    active: dict[str, str] = {}
    for row in storage.list_action_verdicts():
        key = str(row["action_key"])
        if row["verdict"] == MUTE:
            active[key] = "muted"
        elif row["verdict"] == SNOOZE:
            until = str(row["until"] or "")
            if until and until > today:
                active[key] = f"snoozed until {until}"
            else:
                storage.clear_action_verdict(key)
    return active


def filter_actions(actions: list[ActionItem], storage: Storage) -> tuple[list[ActionItem], int]:
    """Apply the user's standing verdicts; returns (kept, suppressed_count)."""
    suppressed = active_suppressions(storage)
    if not suppressed:
        return actions, 0
    kept = [action for action in actions if action.key not in suppressed]
    return kept, len(actions) - len(kept)


def render_verdicts(storage: Storage) -> str:
    active = active_suppressions(storage)
    if not active:
        return "No triage verdicts — every generated action reaches the digest."
    lines = [f"{key}  ({reason})" for key, reason in sorted(active.items())]
    lines.append(
        f"{len(active)} action key(s) suppressed. 'wingman actions unmute <key>' reverses."
    )
    return "\n".join(lines)
