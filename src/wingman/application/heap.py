"""The heap (#113): capture-first inbox for leads, heat-ordered.

Leads arrive in bursts — a job posting, the person who posted it, their
company — with no time to sort which command each artifact belongs to.
`add_to_heap` is unconditional: it never fetches, never spends a token,
and never fails on a weird string — capture always succeeds, and heat
(hot/warm/cold, default warm so capture never blocks on the question)
orders everything downstream. Hot items sitting unsorted earn a digest
nudge (RFC-031 triageable); cold ones never do.

This ships the capture half of #113's spec only: `heap add`, `heap show`,
`heap remove`, and the digest nudge. Classification, screenshot
extraction, company clustering, and confirmation-gated routing (`heap
sort`) are deliberately out of scope here — that is a real extraction
pipeline (model calls, image handling, identity disambiguation) that
deserves its own design pass rather than a rushed first cut riding along
with the capture surface. See issue #113 for the fuller spec.
"""

from __future__ import annotations

from wingman.application.ingest import IngestError
from wingman.domain.heap import HeapHeat, HeapItem
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.heap")

_HEAT_ORDER = {HeapHeat.HOT: 0, HeapHeat.WARM: 1, HeapHeat.COLD: 2}


def add_to_heap(
    items: list[str], storage: Storage, heat: str = "warm", note: str = ""
) -> list[HeapItem]:
    """Capture one or more items into the heap. Unconditional: never
    fetches, never fails on a weird reference — only an empty list or an
    unknown heat value is refused."""
    try:
        heat_value = HeapHeat(heat.strip().lower())
    except ValueError as exc:
        valid = ", ".join(entry.value for entry in HeapHeat)
        raise IngestError(f"unknown heat {heat!r}; use one of: {valid}.") from exc
    cleaned = [entry.strip() for entry in items if entry.strip()]
    if not cleaned:
        raise IngestError("at least one item is required — nothing was captured.")
    saved: list[HeapItem] = []
    for entry in cleaned:
        item = HeapItem(item=entry, heat=heat_value, note=note.strip())
        storage.add_heap_item(item)
        saved.append(item)
    _logger.info("heap captured count=%d heat=%s", len(saved), heat_value.value)
    return saved


def list_heap(storage: Storage) -> list[HeapItem]:
    """Every heap item, hottest first, newest first within a heat band."""
    items = storage.list_heap_items()
    return sorted(items, key=lambda item: (_HEAT_ORDER[item.heat], -item.added_at.timestamp()))


def remove_from_heap(item_id_prefix: str, storage: Storage) -> HeapItem:
    prefix = item_id_prefix.strip()
    if not prefix:
        raise IngestError("item id is empty; see 'wingman heap show' for ids.")
    matches = [item for item in storage.list_heap_items() if item.item_id.startswith(prefix)]
    if not matches:
        raise IngestError(f"no heap item with id {prefix!r}; see 'wingman heap show'.")
    if len(matches) > 1:
        shorts = ", ".join(item.item_id[:8] for item in matches)
        raise IngestError(f"id {prefix!r} is ambiguous ({shorts}); use more characters.")
    storage.delete_heap_item(matches[0].item_id)
    _logger.info("heap item removed id=%s", matches[0].item_id)
    return matches[0]


def hot_unsorted_count(storage: Storage) -> int:
    """How many hot items are sitting in the heap — the digest nudge trigger."""
    return sum(1 for item in storage.list_heap_items() if item.heat is HeapHeat.HOT)


def render_heap(items: list[HeapItem]) -> str:
    if not items:
        return (
            "The heap is empty — 'wingman heap add <url> [<url>...]' "
            "captures leads unconditionally, zero processing at drop time."
        )
    lines = []
    for item in items:
        note = f"  — {item.note}" if item.note else ""
        lines.append(f"{item.item_id[:8]}  [{item.heat.value}]  {item.item}{note}")
    lines.append(f"{len(items)} item(s).")
    return "\n".join(lines)
