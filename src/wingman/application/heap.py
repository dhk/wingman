"""The heap (#113): capture-first inbox for leads, heat-ordered.

Leads arrive in bursts — a job posting, the person who posted it, their
company — with no time to sort which command each artifact belongs to.
`add_to_heap` is unconditional: it never fetches, never spends a token,
and never fails on a weird string — capture always succeeds, and heat
(hot/warm/cold, default warm so capture never blocks on the question)
orders everything downstream. Hot items sitting unsorted earn a digest
nudge (RFC-031 triageable); cold ones never do.

This module is the capture half: `heap add`, `heap show`, `heap remove`,
and the digest nudge. The sort half — classification, company clustering,
near-namesake handling and confirmation-gated routing — lives in
`application/heap_sort.py`, which reads what this writes and mutates
nothing. A dropped local image is archived into the inbox at capture
time, which is what lets a client read it later (#392) without wingman
opening a path outside the workspace.
"""

from __future__ import annotations

from wingman.application.ingest import IngestError
from wingman.domain.heap import HeapHeat, HeapItem
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.heap")

_HEAT_ORDER = {HeapHeat.HOT: 0, HeapHeat.WARM: 1, HeapHeat.COLD: 2}


def add_to_heap(
    items: list[str],
    storage: Storage,
    heat: str = "warm",
    note: str = "",
    config: Config | None = None,
) -> list[HeapItem]:
    """Capture one or more items into the heap. Unconditional: never
    fetches, never fails on a weird reference — only an empty list or an
    unknown heat value is refused.

    A dropped LOCAL IMAGE is copied into the workspace inbox and the
    archived path is what gets stored (#392). Two reasons, and neither is
    tidiness. A screenshot on a phone or a Desktop folder is the most
    deletable file a person owns, and the heap's promise is that nothing
    dropped into it vanishes — a stored path to a file somebody cleared out
    last Tuesday keeps the promise in name only. And it is what makes
    reading one safe: `read_screenshots` will only open a file inside this
    workspace, so a tenant cannot name a path belonging to somebody else on
    a shared process.

    Copying is best-effort. It happens at capture time, and capture is the
    one thing here that must never fail: if the copy does not work the raw
    string is stored exactly as given and the item behaves as it always did.
    """
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
        stored = _archive_if_local_image(entry, config)
        item = HeapItem(item=stored, heat=heat_value, note=note.strip())
        storage.add_heap_item(item)
        saved.append(item)
    _logger.info("heap captured count=%d heat=%s", len(saved), heat_value.value)
    return saved


def _archive_if_local_image(entry: str, config: Config | None) -> str:
    """The archived path for a local image, or the entry unchanged."""
    if config is None:
        return entry
    from wingman.application.heap_sort import archive_name, is_local_image

    source = is_local_image(entry)
    if source is None:
        return entry
    try:
        config.inbox_dir.mkdir(parents=True, exist_ok=True)
        destination = config.inbox_dir / archive_name(source)
        destination.write_bytes(source.read_bytes())
    except OSError as exc:
        # Capture never fails. The original reference is kept, and the item
        # simply is not readable later — which read_screenshots explains.
        _logger.warning("heap could not archive %s (%s) — storing the path as given", entry, exc)
        return entry
    return str(destination)


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
