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
from wingman.domain.profile import ItemStatus, ProfileItem
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
            lines.append(f"  {item.item_id[:8]}  {item.name}{detail}")
    if conflicts:
        lines.append("Conflicts (resolve with 'wingman profile resolve <id>'):")
        for item in conflicts:
            rival = (item.conflicts_with or "?")[:8]
            detail = f" — {item.detail}" if item.detail else ""
            lines.append(f"  {item.item_id[:8]}  {item.name}{detail}  (conflicts with {rival})")
    summary = f"{len(active)} active, {len(conflicts)} in conflict"
    if superseded:
        summary += f", {superseded} superseded by newer document versions (hidden)"
    lines.append(summary + ".")
    return "\n".join(lines)
