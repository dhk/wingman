"""LinkedIn export import: positions, skills, and recommendations as profile items.

Entirely deterministic — the export CSVs are structured data, so no model is
involved (RFC-003). Each consumed CSV becomes an immutable SourceRecord; every
profile item carries verbatim cell text as evidence — the Description cell for
positions (falling back to the Title cell for description-less positions), the
skill name, or the recommendation text. Connections, messages, and other
relationship or tracking files are deliberately not touched here.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from wingman.application.ingest import IngestError
from wingman.application.profile_store import ItemCounts, persist_items
from wingman.domain import ClaimClassification, SourceRecord
from wingman.domain.profile import EvidenceSpan, ProfileItem, ProfileItemKind
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage
from wingman.reporting.career import render_career

_logger = get_logger("application.linkedin")

IMPORT_VERSION = "linkedin_import_v1"
_EXTRACTED_BY = "deterministic/linkedin_import_v1"

POSITIONS_CSV = "Positions.csv"
SKILLS_CSV = "Skills.csv"
RECOMMENDATIONS_CSV = "Recommendations_Received.csv"
CONSUMED_FILES = (POSITIONS_CSV, SKILLS_CSV, RECOMMENDATIONS_CSV)


class LinkedInImportReport(BaseModel):
    positions: int
    skills: int
    recommendations: int
    counts: ItemCounts
    sources_created: int
    career_json_path: Path
    career_md_path: Path


def _read_csv(archive: zipfile.ZipFile, basename: str) -> tuple[str, list[dict[str, str]]] | None:
    """Find a CSV by basename anywhere in the archive; return (raw text, rows)."""
    for entry in sorted(archive.namelist()):
        if Path(entry).name == basename:
            try:
                raw = archive.read(entry).decode("utf-8-sig")
                rows = list(csv.DictReader(io.StringIO(raw)))
            except (UnicodeDecodeError, csv.Error) as exc:
                raise IngestError(
                    f"{entry} in the export could not be parsed ({exc}). "
                    "Nothing was imported from it; re-export from LinkedIn and retry."
                ) from exc
            return raw, rows
    return None


def _persist_csv_source(
    raw: str, basename: str, config: Config, storage: Storage
) -> tuple[SourceRecord, bool]:
    content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    existing = storage.get_source_record_by_hash(content_hash)
    if existing is not None:
        return existing, False
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    stored = config.inbox_dir / f"{stamp}-{content_hash[:8]}-{basename}"
    stored.write_text(raw, encoding="utf-8")
    record = SourceRecord(
        source_type="linkedin_export",
        source_locator=str(stored.relative_to(config.data_dir.resolve()))
        if stored.is_relative_to(config.data_dir.resolve())
        else str(stored),
        content_hash=content_hash,
    )
    storage.add_source_record(record)
    return record, True


def _item(
    kind: ProfileItemKind, name: str, detail: str, quote: str, record: SourceRecord
) -> ProfileItem:
    return ProfileItem(
        kind=kind,
        name=name,
        detail=detail,
        classification=ClaimClassification.FACT,
        confidence=1.0,
        evidence=[EvidenceSpan(source_record_id=record.record_id, quote=quote)],
        prompt_version=IMPORT_VERSION,
        extracted_by=_EXTRACTED_BY,
    )


def _position_items(rows: list[dict[str, str]], record: SourceRecord) -> list[ProfileItem]:
    items: list[ProfileItem] = []
    for row in rows:
        company = (row.get("Company Name") or "").strip()
        title = (row.get("Title") or "").strip()
        if not company or not title:
            continue
        description = (row.get("Description") or "").strip()
        started = (row.get("Started On") or "").strip()
        finished = (row.get("Finished On") or "").strip() or "present"
        period = f" ({started} – {finished})" if started else ""
        items.append(
            _item(
                ProfileItemKind.ROLE,
                f"{title} at {company}",
                f"{description}{period}".strip(),
                description or title,
                record,
            )
        )
    return items


def _skill_items(rows: list[dict[str, str]], record: SourceRecord) -> list[ProfileItem]:
    items: list[ProfileItem] = []
    for row in rows:
        name = (row.get("Name") or "").strip()
        if name:
            items.append(_item(ProfileItemKind.SKILL, name, "", name, record))
    return items


def _recommendation_items(rows: list[dict[str, str]], record: SourceRecord) -> list[ProfileItem]:
    items: list[ProfileItem] = []
    for row in rows:
        text = (row.get("Text") or "").strip()
        first = (row.get("First Name") or "").strip()
        last = (row.get("Last Name") or "").strip()
        if not text or not (first or last):
            continue
        job_title = (row.get("Job Title") or "").strip()
        company = (row.get("Company") or "").strip()
        byline = ", ".join(part for part in (job_title, company) if part)
        recommender = " ".join(part for part in (first, last) if part)
        items.append(
            _item(
                ProfileItemKind.TESTIMONIAL,
                f"Recommendation from {recommender}",
                byline,
                text,
                record,
            )
        )
    return items


def import_linkedin(export_path: Path, config: Config, storage: Storage) -> LinkedInImportReport:
    try:
        archive = zipfile.ZipFile(export_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise IngestError(
            f"could not read {export_path} ({exc}). Nothing was imported; "
            "check the path and re-run 'wingman ingest-linkedin'."
        ) from exc

    builders = {
        POSITIONS_CSV: _position_items,
        SKILLS_CSV: _skill_items,
        RECOMMENDATIONS_CSV: _recommendation_items,
    }
    items: list[ProfileItem] = []
    found = {POSITIONS_CSV: 0, SKILLS_CSV: 0, RECOMMENDATIONS_CSV: 0}
    sources_created = 0
    with archive:
        for basename, builder in builders.items():
            loaded = _read_csv(archive, basename)
            if loaded is None:
                continue
            raw, rows = loaded
            record, created = _persist_csv_source(raw, basename, config, storage)
            sources_created += int(created)
            built = builder(rows, record)
            found[basename] = len(built)
            items.extend(built)
    if not items:
        raise IngestError(
            "no positions, skills, or recommendations were found in the export. "
            "Nothing was imported; check that this is a LinkedIn data export zip."
        )

    counts = persist_items(items, storage)
    career_json, career_md = render_career(
        storage,
        config,
        run_meta={
            "provider": "deterministic",
            "model": IMPORT_VERSION,
            "prompt_version": IMPORT_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
        },
    )
    _logger.info(
        "linkedin_import export=%s positions=%d skills=%d recommendations=%d accepted=%d"
        " skipped=%d merged=%d conflicts=%d sources_created=%d",
        export_path,
        found[POSITIONS_CSV],
        found[SKILLS_CSV],
        found[RECOMMENDATIONS_CSV],
        counts.accepted,
        counts.skipped_duplicates,
        counts.evidence_merged,
        counts.conflicts,
        sources_created,
    )
    return LinkedInImportReport(
        positions=found[POSITIONS_CSV],
        skills=found[SKILLS_CSV],
        recommendations=found[RECOMMENDATIONS_CSV],
        counts=counts,
        sources_created=sources_created,
        career_json_path=career_json,
        career_md_path=career_md,
    )
