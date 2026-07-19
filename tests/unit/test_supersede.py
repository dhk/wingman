"""Document lineage and supersede-on-reingest (RFC-028).

The root cause behind the conflict pile: re-ingesting a newer version of
the same source-of-truth document was treated as a second independent
source, so every rewording became a conflict row and nothing could ever
leave. Records now carry a document_key; a new version's claims replace
the same document's earlier ones, dropped claims are retired, and only
disagreement across DIFFERENT documents remains a conflict.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from wingman.application.ingest import ingest_resume
from wingman.domain.profile import ItemStatus
from wingman.domain.source_record import derive_document_key
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.recorded import RecordedProvider


def _response(items: list[tuple[str, str, str, str]]) -> str:
    """items: (kind, name, detail, quote)."""
    return json.dumps(
        {
            "items": [
                {
                    "kind": kind,
                    "name": name,
                    "detail": detail,
                    "classification": "fact",
                    "confidence": 0.9,
                    "quotes": [quote],
                }
                for kind, name, detail, quote in items
            ]
        }
    )


V1_TEXT = "# Dave\n\n- Led the data platform for two years.\n\nSkills: BigQuery basics.\n"
V2_TEXT = "# Dave\n\n- Led the data platform for three years.\n\nSkills: dbt daily.\n"

V1 = _response(
    [
        ("achievement", "Data platform", "Led it for two years.", "Led the data platform"),
        ("skill", "BigQuery", "Basics.", "Skills: BigQuery basics."),
    ]
)
V2 = _response(
    [
        ("achievement", "Data platform", "Led it for three years.", "Led the data platform"),
        ("skill", "dbt", "Daily.", "Skills: dbt daily."),
    ]
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_derive_document_key() -> None:
    assert derive_document_key("Wingman_Source_of_Truth.md") == "wingman_source_of_truth.md"
    assert (
        derive_document_key("inbox/20260719T152759-ab12cd34-resume.md") == "resume.md"
    )  # file-copy stamp stripped
    assert (
        derive_document_key("20260719T152759123456-google-DOC1.txt") == "google-doc1.txt"
    )  # URL-fetch stamp stripped
    assert derive_document_key("/some/where/resume.md") == "resume.md"


def test_new_version_updates_instead_of_conflicting(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        ingest_resume(_write(tmp_path, "sot.md", V1_TEXT), workspace, storage, RecordedProvider(V1))
        report = ingest_resume(
            _write(tmp_path, "sot.md", V2_TEXT), workspace, storage, RecordedProvider(V2)
        )
        assert report.conflicts == 0
        assert report.updated == 1  # Data platform: two years -> three years
        assert report.accepted == 1  # dbt is new
        assert report.retired == 1  # BigQuery dropped from the document
        active = [i for i in storage.list_profile_items() if i.status is ItemStatus.ACTIVE]
        assert {(i.name, i.detail) for i in active} == {
            ("Data platform", "Led it for three years."),
            ("dbt", "Daily."),
        }
    markdown = report.career_md_path.read_text(encoding="utf-8")
    assert "three years" in markdown
    assert "Conflicts (need your resolution)" not in markdown
    assert "BigQuery" not in markdown


def test_superseded_items_are_kept_for_provenance(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        ingest_resume(_write(tmp_path, "sot.md", V1_TEXT), workspace, storage, RecordedProvider(V1))
        old = {i.item_id for i in storage.list_profile_items()}
        ingest_resume(_write(tmp_path, "sot.md", V2_TEXT), workspace, storage, RecordedProvider(V2))
        for item_id in old:  # still resolvable by id (old packs/assessments cite them)
            kept = storage.get_profile_item(item_id)
            assert kept is not None and kept.status is ItemStatus.SUPERSEDED


def test_third_version_drains_a_pre_existing_conflict_pile(
    workspace: Config, tmp_path: Path
) -> None:
    """Workspaces already carrying pre-RFC-028 conflict rows heal on next ingest."""
    from wingman.application.profile_store import persist_items
    from wingman.application.ingest import _persist_source

    with Storage(workspace.db_path) as storage:
        # Simulate the pre-fix pile: two versions persisted with no lineage.
        ingest_resume(_write(tmp_path, "sot.md", V1_TEXT), workspace, storage, RecordedProvider(V1))
        record, _ = _persist_source(
            _write(tmp_path, "sot.md", V1_TEXT + "\nrev2\n"),
            V1_TEXT + "\nrev2\n",
            workspace,
            storage,
        )
        from wingman.domain.profile import EvidenceSpan, ProfileItem, ProfileItemKind
        from wingman.domain.provenance import ClaimClassification

        persist_items(
            [
                ProfileItem(
                    kind=ProfileItemKind.SKILL,
                    name="BigQuery",
                    detail="Rephrased.",
                    classification=ClaimClassification.FACT,
                    confidence=0.9,
                    evidence=[EvidenceSpan(source_record_id=record.record_id, quote="q")],
                    prompt_version="v",
                    extracted_by="t",
                )
            ],
            storage,  # no lineage passed: the old behavior, a conflict row
        )
        conflicts = [i for i in storage.list_profile_items() if i.status is ItemStatus.CONFLICT]
        assert len(conflicts) == 1
        # Now a v3 ingest with lineage: the pile drains.
        report = ingest_resume(
            _write(tmp_path, "sot.md", V2_TEXT), workspace, storage, RecordedProvider(V2)
        )
        assert report.retired >= 2  # dropped BigQuery active AND its stale conflict row
        assert not [i for i in storage.list_profile_items() if i.status is ItemStatus.CONFLICT]


def test_cross_source_evidence_still_conflicts(workspace: Config, tmp_path: Path) -> None:
    """An item vouched for by ANOTHER document is never silently replaced."""
    with Storage(workspace.db_path) as storage:
        ingest_resume(_write(tmp_path, "sot.md", V1_TEXT), workspace, storage, RecordedProvider(V1))
        # A different document independently claims the same BigQuery skill.
        other_text = V1_TEXT + "\nAlso in recruiter notes.\n"
        other = _write(tmp_path, "recruiter-notes.md", other_text)
        ingest_resume(
            other,
            workspace,
            storage,
            RecordedProvider(
                _response([("skill", "BigQuery", "Basics.", "Skills: BigQuery basics.")])
            ),
        )
        # v2 of the source of truth rewords BigQuery: the item now carries
        # recruiter-notes evidence too, so this is a conflict, not an update.
        v2 = _response([("skill", "BigQuery", "Expert.", "Skills: dbt daily.")])
        report = ingest_resume(
            _write(tmp_path, "sot.md", V2_TEXT), workspace, storage, RecordedProvider(v2)
        )
        assert report.conflicts == 1 and report.updated == 0


def test_identical_reingest_stays_idempotent(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        path = _write(tmp_path, "sot.md", V1_TEXT)
        provider = RecordedProvider(V1)
        ingest_resume(path, workspace, storage, provider)
        second = ingest_resume(path, workspace, storage, provider)
        assert second.source_reused is True
        assert second.skipped_duplicates == 2
        assert second.updated == 0 and second.retired == 0
        assert storage.count_profile_items() == 2


def test_migration_backfills_document_key(tmp_path: Path) -> None:
    """A pre-RFC-028 database gains the column and derived keys on open."""
    db = tmp_path / "old.db"
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE source_records (record_id TEXT PRIMARY KEY, source_type TEXT NOT NULL,"
        " source_locator TEXT NOT NULL, content_hash TEXT NOT NULL, source_timestamp TEXT,"
        " ingested_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO source_records VALUES"
        " ('r1', 'resume', 'inbox/20260718T101010-ab12cd34-sot.md', 'h1', NULL,"
        " '2026-07-18T10:10:10+00:00')"
    )
    connection.commit()
    connection.close()
    with Storage(db) as storage:
        record = storage.get_source_record("r1")
        assert record is not None and record.document_key == "sot.md"
        assert storage.record_ids_for_document("sot.md") == {"r1"}
