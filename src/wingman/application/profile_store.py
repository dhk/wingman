"""Shared persistence for validated profile items: dedupe, evidence merge, conflicts."""

from __future__ import annotations

from pydantic import BaseModel

from wingman.domain.profile import ItemStatus, ProfileItem
from wingman.infrastructure.storage import Storage


class ItemCounts(BaseModel):
    accepted: int = 0
    skipped_duplicates: int = 0
    evidence_merged: int = 0
    conflicts: int = 0


def persist_items(items: list[ProfileItem], storage: Storage) -> ItemCounts:
    """Store validated items: identical ones dedupe, same-value-new-evidence merges
    provenance into the existing item, conflicting values are kept side by side."""
    counts = ItemCounts()
    for item in items:
        existing = storage.find_active_item(item.kind, item.name_key)
        if existing is not None:
            if existing.detail == item.detail and existing.classification == item.classification:
                new_spans = [span for span in item.evidence if span not in existing.evidence]
                if new_spans:
                    storage.update_profile_item(
                        existing.model_copy(update={"evidence": existing.evidence + new_spans})
                    )
                    counts.evidence_merged += 1
                else:
                    counts.skipped_duplicates += 1
                continue
            item = item.model_copy(
                update={"status": ItemStatus.CONFLICT, "conflicts_with": existing.item_id}
            )
            counts.conflicts += 1
        else:
            counts.accepted += 1
        storage.add_profile_item(item)
    return counts
