"""Career-profile management: list, remove, resolve, clear (RFC-027).

The manual correction workflow Phase 1 promised and never got: re-ingesting
an evolving source-of-truth document accumulates items — the same skill
re-proposed with different wording is stored as a side-by-side conflict
row — and until now nothing short of raw SQL could remove or resolve one.

Every mutation re-renders career.json/career.md, so the artifacts never
drift from the table. Items are addressed by any unambiguous item_id
prefix (git-style), because nobody types a full UUID.
"""

from __future__ import annotations

from datetime import UTC, datetime

from wingman.application.ingest import IngestError
from wingman.domain.profile import ItemStatus, ProfileItem, ProfileItemKind
from wingman.domain.provenance import FORM_EXTRACTOR
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
                f"{_value_note(item)}{_arrival_note(item)}"
            )
    if conflicts:
        lines.append("Conflicts (resolve with 'wingman profile resolve <id>'):")
        for item in conflicts:
            rival = (item.conflicts_with or "?")[:8]
            detail = f" — {item.detail}" if item.detail else ""
            lines.append(
                f"  {item.item_id[:8]}  {item.name}{detail}{_scale_tags(item)}"
                f"{_value_note(item)}{_arrival_note(item)}  (conflicts with {rival})"
            )
    summary = f"{len(active)} active, {len(conflicts)} in conflict"
    if superseded:
        summary += f", {superseded} superseded by newer document versions (hidden)"
    lines.append(summary + ".")
    return "\n".join(lines)
