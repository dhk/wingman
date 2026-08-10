"""Shared persistence for validated profile items: dedupe, merge, supersede, conflict.

The supersede rule (RFC-028) is what keeps an evolving source-of-truth
document from piling up conflicts: a claim whose evidence comes entirely
from earlier versions of the document being ingested is that document's
own earlier word, and the new version simply replaces it. A claim with
evidence from anywhere else is a genuine cross-source disagreement and is
kept side by side as a conflict for the user to resolve.

Stated more generally, and this is the form the rule actually needs: a
document never conflicts with ITSELF, and never corroborates itself
either. Re-ingesting an unchanged file reuses its source record, so the
superseded-record set comes back empty and the lineage check above cannot
fire — which left the model's own paraphrase drift between two runs to be
stored as a contradiction. And a claim restated in a newer version of the
same document is the same source saying the same thing again, not a
second voucher for it (#336).
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


def _document_keys(item: ProfileItem, storage: Storage, cache: dict[str, str]) -> set[str]:
    """The documents behind an item's evidence, by document_key.

    A record with no document_key is not a document — interview captures
    and manual notes carry an empty one — and must never be treated as
    sharing an identity with another. Returned empty for those, so the
    same-document rules below simply do not apply to them.
    """
    keys: set[str] = set()
    for span in item.evidence:
        record_id = span.source_record_id
        if record_id not in cache:
            record = storage.get_source_record(record_id)
            cache[record_id] = record.document_key if record is not None else ""
        key = cache[record_id]
        if key:
            keys.add(key)
    return keys


def _same_document(incoming: set[str], existing: set[str]) -> bool:
    """Both claims rest on one and the same document, and nothing else.

    RFC-028 already says a document only speaks for itself. This is that
    rule stated in terms of the DOCUMENT rather than a set of superseded
    record ids, which is what it was missing: re-ingesting an unchanged
    file reuses its source record, so the superseded set (which excludes
    the record being ingested) comes back empty and the lineage check
    could not fire — leaving the model's own paraphrase drift between two
    runs to be stored as a contradiction (#336).
    """
    return bool(incoming) and incoming == existing


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
    document_keys: dict[str, str] = {}
    for item in items:
        existing = storage.find_active_item(item.kind, item.name_key)
        if existing is not None:
            incoming_docs = _document_keys(item, storage, document_keys)
            existing_docs = _document_keys(existing, storage, document_keys)
            if (
                existing.detail == item.detail
                and existing.classification == item.classification
                and existing.intensity == item.intensity
                and existing.company_reason == item.company_reason
            ):
                # A claim restated in a new version of the SAME document is
                # not better supported than it was before — it is one source
                # saying the same thing again, and stacking a second span
                # would make a re-ingest look like corroboration (#336).
                #
                # So the incoming document REPLACES its own earlier spans
                # rather than adding to them: the count stays one voucher per
                # document, and the quote is the current version's words
                # instead of a line that may no longer be in the file.
                # Evidence from any OTHER document is untouched — that is
                # real corroboration and the whole point of merging.
                if incoming_docs:
                    kept = [
                        span
                        for span in existing.evidence
                        if document_keys.get(span.source_record_id, "") not in incoming_docs
                    ]
                    merged = kept + list(item.evidence)
                else:
                    # No document behind this one (an interview capture, a
                    # manual note): nothing to replace, so append as before.
                    merged = list(existing.evidence) + [
                        span for span in item.evidence if span not in existing.evidence
                    ]
                if merged != list(existing.evidence):
                    storage.update_profile_item(existing.model_copy(update={"evidence": merged}))
                    counts.evidence_merged += 1
                else:
                    counts.skipped_duplicates += 1
                continue
            if _superseded_by_lineage(existing, superseded) or _same_document(
                incoming_docs, existing_docs
            ):
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
