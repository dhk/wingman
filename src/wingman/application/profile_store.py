"""Shared persistence for validated profile items: dedupe, merge, supersede, conflict.

The supersede rule (RFC-028) is what keeps an evolving source-of-truth
document from piling up conflicts: a claim whose evidence comes entirely
from earlier versions of the document being ingested is that document's
own earlier word, and the new version simply replaces it. A claim with
evidence from anywhere else is a genuine cross-source disagreement and is
kept side by side as a conflict for the user to resolve.
"""

from __future__ import annotations

from pydantic import BaseModel

from wingman.domain.profile import ItemStatus, ProfileItem
from wingman.infrastructure.storage import Storage


class ItemCounts(BaseModel):
    accepted: int = 0
    skipped_duplicates: int = 0
    evidence_merged: int = 0
    conflicts: int = 0
    updated: int = 0
    retired: int = 0


def _superseded_by_lineage(item: ProfileItem, superseded_records: set[str]) -> bool:
    """True when every piece of the item's evidence is an earlier version
    of the document now being ingested — nothing else vouches for it."""
    return bool(superseded_records) and all(
        span.source_record_id in superseded_records for span in item.evidence
    )


def persist_items(
    items: list[ProfileItem],
    storage: Storage,
    superseded_records: set[str] | None = None,
) -> ItemCounts:
    """Store validated items: identical ones dedupe, same-value-new-evidence merges
    provenance into the existing item, conflicting values are kept side by side —
    unless the existing value rests entirely on earlier versions of the same
    document (superseded_records), in which case the new version replaces it
    ('updated'). Afterwards, items resting entirely on those earlier versions
    that the new version no longer proposes are retired ('retired'): the
    document is declarative about its own claims (RFC-028).
    """
    superseded = superseded_records or set()
    counts = ItemCounts()
    for item in items:
        existing = storage.find_active_item(item.kind, item.name_key)
        if existing is not None:
            if (
                existing.detail == item.detail
                and existing.classification == item.classification
                and existing.intensity == item.intensity
                and existing.company_reason == item.company_reason
            ):
                new_spans = [span for span in item.evidence if span not in existing.evidence]
                if new_spans:
                    storage.update_profile_item(
                        existing.model_copy(update={"evidence": existing.evidence + new_spans})
                    )
                    counts.evidence_merged += 1
                else:
                    counts.skipped_duplicates += 1
                continue
            if _superseded_by_lineage(existing, superseded):
                storage.update_profile_item(
                    existing.model_copy(update={"status": ItemStatus.SUPERSEDED})
                )
                counts.updated += 1
            else:
                item = item.model_copy(
                    update={"status": ItemStatus.CONFLICT, "conflicts_with": existing.item_id}
                )
                counts.conflicts += 1
        else:
            counts.accepted += 1
        storage.add_profile_item(item)

    if superseded:
        # Retirement sweep: anything still resting entirely on the replaced
        # versions was either dropped from the document or is a stale
        # conflict row from before this ingest. Items touched above are
        # safe by construction — they carry a span from the current record.
        for leftover in storage.list_profile_items():
            if leftover.status is ItemStatus.SUPERSEDED:
                continue
            if _superseded_by_lineage(leftover, superseded):
                storage.update_profile_item(
                    leftover.model_copy(update={"status": ItemStatus.SUPERSEDED})
                )
                counts.retired += 1
    return counts
