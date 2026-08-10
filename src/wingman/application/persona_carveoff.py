"""Carve off a coached persona's captured interview data into its own
Wingman workspace, brand-new OR already-populated — #235
(docs/COACHING-MODE-DESIGN.md).

Coaching Mode lets a coach run persona-scoped interview capture ABOUT
someone else (`application/interview.py`, `ProfileItem.persona_id`), inside
the coach's own workspace. Today that data has no path out: if the persona
(a real person) wants to start using Wingman themselves, they start from an
empty profile and everything already captured is stranded. This module
exports a persona's captured items and writes them into a target
workspace's own first-person profile — Phase 1 (#238) covered a
brand-new/empty target; Phase 2 (RFC-054) lifts that restriction:
`seed_new_workspace` now writes into an ALREADY-POPULATED target too, via
the exact same `profile_store.persist_items` call (RFC-028's
dedup/supersede/conflict machinery) every other ingestion path in this
codebase already uses for "a new item that might collide with something
already there." A contradiction between carved-off data and an existing
item is never silently overwritten — it lands as a `CONFLICT` row the
person resolves themselves (`wingman profile list`/`resolve`), the same as
any other conflicting ingest.

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
    nothing is written to any workspace by producing this; reused
    unmodified by `seed_new_workspace` for both a brand-new and an
    already-populated target (RFC-054)."""

    persona_name: str
    items: list[ProfileItem]
    source_records: list[SourceRecord]


class CarveOffReport(BaseModel):
    persona_name: str
    target_data_dir: str
    counts: ItemCounts
    # Named for what it counts: every evidence record the carve-off brought
    # across and the target now has. A re-run writes none of them a second
    # time (they are already there, skipped by record_id), and reporting
    # "0 evidence records preserved" then would be false in the other
    # direction — the evidence IS preserved, it just did not need writing.
    source_records_preserved: int


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
    OWN first-person profile — whether that workspace is brand-new/empty
    (Phase 1, #238) or already has its own profile items (Phase 2,
    RFC-054). Both go through the same `profile_store.persist_items` call
    every other ingestion path in this codebase already uses for "a new
    item that might dedupe, supersede, or conflict with something already
    there": an item matching nothing existing is added; one matching an
    existing item's value merges evidence or is skipped as a duplicate;
    one that genuinely disagrees with an existing item lands as a
    side-by-side `CONFLICT` row for the person to resolve themselves
    (`wingman profile list`/`resolve`) — never silently overwritten.

    Placeholder source records (RFC-049) reuse the SAME `record_id` every
    time the same persona is exported, so carving the same persona off
    into the same already-populated target a second time (e.g. a re-run
    after coaching more of them) would otherwise hit a source-record-id
    collision even though the content is identical; already-present
    placeholder ids are skipped rather than re-inserted.

    Carried-over ITEMS need the same treatment, for a reason that only
    shows up once a re-run meets an unresolved conflict. A rehomed item
    keeps its original `item_id`, and `persist_items` looks for a rival
    among ACTIVE items only — so a conflict row written by the first run
    is invisible to the second, which dutifully marks the same export as
    a conflict again and tries to insert an `item_id` that is already
    there. The person could not re-run at all until they had resolved a
    conflict they may not have looked at yet.

    Skipping is the honest answer rather than overwriting: the row is
    already in that workspace awaiting their decision, re-importing
    identical content cannot improve it, and replacing a row they may
    have already started acting on would be worse than leaving it. A
    genuinely CHANGED claim arrives as a new item with a new id, and
    conflicts through the ordinary machinery like anything else.
    """
    for record in export.source_records:
        if target_storage.get_source_record(record.record_id) is None:
            target_storage.add_source_record(record)
    fresh, already_there = [], 0
    for item in export.items:
        if target_storage.get_profile_item(item.item_id) is None:
            fresh.append(item)
        else:
            already_there += 1
    counts = persist_items(fresh, target_storage)
    counts.skipped_duplicates += already_there
    return counts


def _refuse_another_tenants_workspace(target_dir: Path, source_config: Config) -> None:
    """Keep a carve-off inside the caller's own tenancy (#333).

    The shared process (RFC-048) runs as ONE account that owns every
    tenant's workspace, so a path is not an access control here — the
    process can write all of them. Under the retired shape-B instances the
    Unix account made this impossible; it is reachable now, and it writes
    profile items, source records and conflict rows into somebody else's
    live profile.

    It is not a malice-only path either: an operator carving a persona and
    mistyping a directory lands in the same place, silently, because the
    target is created when absent.

    Targets OUTSIDE the registry stay legal — carving into a brand-new
    directory that is not yet a tenant is the documented flow, and the
    report this command prints says exactly that. What is refused is a
    target that is, contains, or sits inside another registered tenant's
    data_dir.

    Distinct from #271's `privileged` flag, which is authorization: this
    is containment, and it must keep holding for a privileged caller and
    for any future caller that forgets to check the flag.
    """
    from wingman.infrastructure.tenants import (
        TenantRegistryError,
        is_tenant_config,
        load_registry,
        tenant_registry_path,
    )

    # Only a tenant-bound config shares a process with anybody. A solo
    # install owns its own machine and has no registry to consult — and, on
    # a box that happens to host tenants under another account, cannot read
    # one anyway. Checking unconditionally made every solo carve-off depend
    # on a root-owned file.
    if not is_tenant_config(source_config):
        return

    registry_path = tenant_registry_path()
    try:
        tenants = load_registry(registry_path)
    except TenantRegistryError as exc:
        # A registry we cannot read is not a licence to write anywhere. Solo
        # installs are unaffected: an ABSENT registry parses as no tenants.
        raise IngestError(
            f"the tenant registry ({registry_path}) could not be read, so this cannot check "
            f"that the target is not another tenant's workspace: {exc}"
        ) from exc

    own = source_config.data_dir.expanduser().resolve()
    for tenant in tenants:
        other = tenant.data_dir.expanduser().resolve()
        if other == own:
            continue
        if target_dir == other or other in target_dir.parents or target_dir in other.parents:
            raise IngestError(
                f"the target {target_dir} belongs to the tenant {tenant.slug!r} — a carve-off "
                "writes a profile into the target workspace, and that one is not yours. Point "
                "it at a new directory instead; it becomes reachable once somebody registers "
                "it as a tenant."
            )


def carve_off_persona(
    name_or_id: str, source_config: Config, source_storage: Storage, target_data_dir: Path
) -> CarveOffReport:
    """The end-to-end operation the CLI/MCP surface drives: export the
    persona from the caller's own workspace, then write it into
    `target_data_dir` (created if needed) — a brand-new/empty workspace or
    one that already has its own profile, either way, via
    `seed_new_workspace`.
    """
    target_dir = target_data_dir.expanduser().resolve()
    if target_dir == source_config.data_dir.resolve():
        raise IngestError(
            "the target must be a different workspace than your own — point it at a "
            "brand-new directory (e.g. what a fresh $WINGMAN_DATA_DIR would point at), "
            "not your current workspace."
        )
    _refuse_another_tenants_workspace(target_dir, source_config)
    export = export_persona(name_or_id, source_storage)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_config = Config(data_dir=target_dir, data_dir_source="persona carve-off target")
    # Same directories 'wingman init' creates (cli.main._workspace_dirs):
    # a conflict landing here needs 'wingman profile resolve' to work right
    # away, and that re-renders career.md/json into reports_dir — which a
    # target this command created from scratch would not otherwise have.
    target_config.inbox_dir.mkdir(parents=True, exist_ok=True)
    target_config.reports_dir.mkdir(parents=True, exist_ok=True)
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
        source_records_preserved=len(export.source_records),
    )


def render_carveoff_report(report: CarveOffReport) -> str:
    """The carve-off command's own output — including, if the target
    already had its own data, how the merge actually went: how many items
    landed cleanly vs. how many surfaced as a conflict needing
    `wingman profile list`/`resolve`, never a report that's silent about a
    partial or contentious outcome (RFC-054)."""
    counts = report.counts
    pieces = [f"{counts.accepted} new profile item(s) added"]
    if counts.evidence_merged:
        pieces.append(f"{counts.evidence_merged} merged into matching existing item(s)")
    if counts.skipped_duplicates:
        pieces.append(f"{counts.skipped_duplicates} already present (skipped)")
    if counts.updated:
        pieces.append(f"{counts.updated} superseded an earlier version")
    if counts.conflicts:
        pieces.append(f"{counts.conflicts} in CONFLICT with existing data")
    summary = ", ".join(pieces)
    lines = [
        (
            f"Carved off {report.persona_name!r} into {report.target_data_dir}: {summary}. "
            f"{report.source_records_preserved} evidence record(s) preserved."
        )
    ]
    if counts.conflicts:
        lines.append(
            f"{counts.conflicts} item(s) contradicted data already in that workspace and were "
            "NOT overwritten — they're surfaced as conflicts for the person to resolve "
            "themselves: run 'wingman profile list' with WINGMAN_DATA_DIR pointed at "
            f"{report.target_data_dir}, then 'wingman profile resolve <id>' to settle each "
            "one. (The directory you happen to be standing in is never how a workspace "
            "gets chosen — an installed CLI must not scatter data wherever it is run, so "
            "load_config reads the tenant binding or WINGMAN_DATA_DIR and nothing else.)"
        )
    lines.append(
        "If this target workspace isn't already registered as a tenant, it has no live URL "
        "yet — nothing serves it until it is (root-run: 'wingman-add-tenant.sh <slug>' "
        f"pointed at {report.target_data_dir}); once it is (or if it already was), 'wingman "
        "tenant urls <slug>' (or 'wingman tenant urls' for every tenant) prints its "
        "connector URLs."
    )
    return "\n".join(lines)


__all__ = [
    "PLACEHOLDER_SOURCE_TYPE",
    "CarveOffExport",
    "CarveOffReport",
    "carve_off_persona",
    "export_persona",
    "render_carveoff_report",
    "seed_new_workspace",
]
