"""Carve off a coached persona's captured interview data into its own,
brand-new Wingman workspace — Phase 1 of #235 (docs/COACHING-MODE-DESIGN.md).

Coaching Mode lets a coach run persona-scoped interview capture ABOUT
someone else (`application/interview.py`, `ProfileItem.persona_id`), inside
the coach's own workspace. Today that data has no path out: if the persona
(a real person) wants to start using Wingman themselves, they start from an
empty profile and everything already captured is stranded. This module is
the first of two planned phases (#235): export a persona's captured items
and seed a BRAND-NEW workspace with them as that workspace's own
first-person profile. Phase 2 — merging into an ALREADY-POPULATED target
workspace, via RFC-028's supersede/conflict rule so contradictions become a
CONFLICT the person resolves themselves — is a planned, separate follow-up,
not built here; `seed_new_workspace` refuses outright rather than silently
guessing at a merge.

**Evidence resolvability (RFC-049).** `ProfileItem.evidence` is
`list[EvidenceSpan]`, `min_length=1`, never optional — every item carries a
`source_record_id` pointing at a `SourceRecord` that, for coaching
captures, lives only in the COACH's own workspace (`interview.py` writes
one there per capture). Copying a ProfileItem as-is into a brand-new target
workspace would leave that reference dangling: nothing in the target
workspace's `source_records` table has that id. Rather than either (a)
copying the coach's real source records — that would leak the coach's own
inbox files/notes, which do not belong to the persona, into their new
workspace — or silently relaxing the invariant, every referenced
`source_record_id` gets an honestly-labeled PLACEHOLDER `SourceRecord` in
the target, reusing the SAME id (so no evidence span needs rewriting): its
`source_type` is `persona_carveoff_placeholder` and its `source_locator`
says in plain words that this item was carved off from a coach's workspace
and the original record was not copied, alongside what's known about that
original record (its source_type/locator) for context. The evidence QUOTE
itself needs no copying — it already lives verbatim in `ProfileItem.detail`
/ `EvidenceSpan.quote`, copied along with the item — so nothing about
`career.md`'s citation rendering (`reporting/career.py`, which cites by id
and prints the quote from the ProfileItem itself, never by dereferencing
the source record's own content) changes or breaks. `storage.get_source_record`
callers elsewhere (e.g. `linkedin.py`'s supersession lookup) also keep
working: they get a real row back, just one that is honest about being a
placeholder rather than `None`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel

from wingman.application.ingest import IngestError
from wingman.application.profile_store import ItemCounts, persist_items
from wingman.domain.persona import Persona
from wingman.domain.profile import ItemStatus, ProfileItem
from wingman.domain.source_record import SourceRecord
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.persona_carveoff")

# RFC-049: a placeholder record honestly marks itself as such rather than
# pretending to be the coach's original.
PLACEHOLDER_SOURCE_TYPE = "persona_carveoff_placeholder"


class CarveOffExport(BaseModel):
    """A persona's exportable profile: their own ACTIVE items, rehomed as
    first-person items (persona_id cleared), plus one placeholder
    SourceRecord per evidence span they cite (RFC-049). Read-only —
    nothing is written to any workspace by producing this; Phase 2 (#235)
    is expected to reuse this same export for the merge-into-an-existing-
    workspace path."""

    persona_name: str
    items: list[ProfileItem]
    source_records: list[SourceRecord]


class CarveOffReport(BaseModel):
    persona_name: str
    target_data_dir: str
    counts: ItemCounts
    source_records_written: int


def _resolve_persona(name_or_id: str, storage: Storage) -> Persona:
    name_or_id = name_or_id.strip()
    if not name_or_id:
        raise IngestError("a persona name (or id) is required — nothing was carved off.")
    persona = storage.find_persona_by_name(name_or_id) or storage.get_persona(name_or_id)
    if persona is None:
        raise IngestError(
            f"no persona named or id'd {name_or_id!r}; see 'wingman coach-persona list'."
        )
    return persona


def _rehome_item(item: ProfileItem, persona: Persona) -> ProfileItem:
    """This persona's item, stripped back to a first-person item for the
    target workspace: persona_id cleared, and the '(persona:<id>)' name
    suffix `interview.py`'s `_item_name` appends to keep persona-scoped
    name_keys distinct from the coach's own removed. Without stripping it,
    the target workspace's OWN future captures — which always use
    persona_id=None there, so never produce that suffix — would silently
    fail to match/supersede/conflict against this carried-over item by
    name_key, instead of participating in that machinery like every other
    first-person item does."""
    suffix = f" (persona:{persona.persona_id})"
    name = item.name.removesuffix(suffix)
    return item.model_copy(update={"persona_id": None, "name": name, "conflicts_with": None})


def _placeholder_record(
    record_id: str, original: SourceRecord | None, persona: Persona
) -> SourceRecord:
    """See module docstring (RFC-049): an honestly-labeled stand-in for a
    coach-workspace source record, reusing the SAME record_id so no
    evidence span needs rewriting. The evidence quote itself needs no
    copying — it already lives verbatim in the ProfileItem being carried
    over alongside this record."""
    if original is not None:
        locator = (
            f"Carved off from a coach's workspace (persona {persona.name!r}). "
            f"Original source_type={original.source_type!r}, "
            f"locator={original.source_locator!r} — the coach's own record was not "
            "copied here; the evidence quote is preserved verbatim on the profile item."
        )
    else:
        locator = (
            f"Carved off from a coach's workspace (persona {persona.name!r}). "
            "The original source record could not be found in the coach's workspace "
            "either — the evidence quote is preserved verbatim on the profile item."
        )
    content_hash = hashlib.sha256(f"carveoff:{persona.persona_id}:{record_id}".encode()).hexdigest()
    return SourceRecord(
        record_id=record_id,
        source_type=PLACEHOLDER_SOURCE_TYPE,
        source_locator=locator,
        content_hash=content_hash,
        document_key="",
    )


def export_persona(name_or_id: str, storage: Storage) -> CarveOffExport:
    """Every ACTIVE ProfileItem captured under this persona in the
    CALLER's own workspace, rehomed as first-person items plus
    placeholder source records for their evidence (RFC-049). Read-only.

    Superseded and conflict-losing items are deliberately excluded: they
    are not the persona's current, live understanding, and a carried-over
    CONFLICT row's `conflicts_with` id would dangle once rehomed into a
    workspace that never had the rival item to begin with.
    """
    persona = _resolve_persona(name_or_id, storage)
    items = [
        item
        for item in storage.list_profile_items()
        if item.persona_id == persona.persona_id and item.status is ItemStatus.ACTIVE
    ]
    if not items:
        raise IngestError(
            f"persona {persona.name!r} has no captured (active) profile items yet — "
            "nothing to carve off."
        )
    rehomed = [_rehome_item(item, persona) for item in items]
    referenced_ids = sorted({span.source_record_id for item in rehomed for span in item.evidence})
    records = [
        _placeholder_record(record_id, storage.get_source_record(record_id), persona)
        for record_id in referenced_ids
    ]
    _logger.info(
        "persona_export persona_id=%s name=%r items=%d records=%d",
        persona.persona_id,
        persona.name,
        len(rehomed),
        len(records),
    )
    return CarveOffExport(persona_name=persona.name, items=rehomed, source_records=records)


def seed_new_workspace(export: CarveOffExport, target_storage: Storage) -> ItemCounts:
    """Write a persona's carved-off export into a TARGET workspace as ITS
    OWN first-person profile (Phase 1 of #235 — see module docstring).

    Refuses outright if the target workspace already has any profile
    items: merging carved-off data into an ALREADY-POPULATED workspace is
    Phase 2 of #235 (a planned, separate follow-up using RFC-028's
    supersede/conflict rule), not yet built. This only ever seeds a
    brand-new/empty target — fail loud, never a silent no-op or a guess
    at merging.
    """
    if target_storage.count_profile_items() > 0:
        raise IngestError(
            "the target workspace already has profile items — carrying a persona's data "
            "into an ALREADY-POPULATED workspace is Phase 2 of #235 (merge, using "
            "RFC-028's conflict rule), not yet built. Nothing was written; point the "
            "target at a brand-new, empty workspace instead."
        )
    for record in export.source_records:
        target_storage.add_source_record(record)
    counts = persist_items(export.items, target_storage)
    return counts


def carve_off_persona(
    name_or_id: str, source_config: Config, source_storage: Storage, target_data_dir: Path
) -> CarveOffReport:
    """The end-to-end Phase-1 operation the CLI/MCP surface drives: export
    the persona from the caller's own workspace, then seed `target_data_dir`
    (created if needed) as a brand-new workspace with it. See
    `seed_new_workspace` for the non-empty-target refusal.
    """
    target_dir = target_data_dir.expanduser().resolve()
    if target_dir == source_config.data_dir.resolve():
        raise IngestError(
            "the target must be a different workspace than your own — point it at a "
            "brand-new directory (e.g. what a fresh $WINGMAN_DATA_DIR would point at), "
            "not your current workspace."
        )
    export = export_persona(name_or_id, source_storage)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_config = Config(data_dir=target_dir, data_dir_source="persona carve-off target")
    with Storage(target_config.db_path) as target_storage:
        counts = seed_new_workspace(export, target_storage)
    _logger.info(
        "persona_carveoff persona_name=%r target=%s accepted=%d",
        export.persona_name,
        target_dir,
        counts.accepted,
    )
    return CarveOffReport(
        persona_name=export.persona_name,
        target_data_dir=str(target_dir),
        counts=counts,
        source_records_written=len(export.source_records),
    )


def render_carveoff_report(report: CarveOffReport) -> str:
    return (
        f"Carved off {report.persona_name!r} into a new workspace at "
        f"{report.target_data_dir}: {report.counts.accepted} profile item(s) seeded, "
        f"{report.source_records_written} evidence record(s) preserved. "
        "(Phase 1 of #235 — a brand-new workspace only; merging into an existing one is "
        "a planned Phase 2 follow-up.) No live URL yet — this workspace isn't served by "
        "anything until it's registered as a tenant (root-run: "
        f"'wingman-add-tenant.sh <slug>' pointed at {report.target_data_dir}); once it is, "
        "'wingman tenant urls <slug>' (or 'wingman tenant urls' for every tenant) prints its "
        "connector URLs."
    )


__all__ = [
    "PLACEHOLDER_SOURCE_TYPE",
    "CarveOffExport",
    "CarveOffReport",
    "carve_off_persona",
    "export_persona",
    "render_carveoff_report",
    "seed_new_workspace",
]
