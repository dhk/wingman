import hashlib
import json
from pathlib import Path

import pytest

from wingman.application.ingest import IngestError, ingest_resume
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.recorded import RecordedProvider

RESUME = "# Jo\n\n- Shipped the search rewrite.\n\nSkills: Python.\n"
RESPONSE = json.dumps(
    {
        "items": [
            {
                "kind": "achievement",
                "name": "Search rewrite",
                "detail": "Shipped the search rewrite.",
                "classification": "fact",
                "confidence": 0.9,
                "quotes": ["Shipped the search rewrite."],
            },
            {
                "kind": "skill",
                "name": "Python",
                "detail": "",
                "classification": "fact",
                "confidence": 0.95,
                "quotes": ["Skills: Python."],
            },
        ]
    }
)


@pytest.fixture
def workspace(tmp_path: Path) -> Config:
    config = load_config(env={ENV_DATA_DIR: str(tmp_path / "ws")})
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def _resume_file(tmp_path: Path, text: str = RESUME) -> Path:
    path = tmp_path / "resume.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_full_slice_produces_cited_artifacts(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        report = ingest_resume(
            _resume_file(tmp_path), workspace, storage, RecordedProvider(RESPONSE)
        )
        assert report.accepted == 2
        assert report.rejected == []
        assert storage.count_source_records() == 1
        assert storage.count_profile_items() == 2

    payload = json.loads(report.career_json_path.read_text(encoding="utf-8"))
    assert len(payload["achievements"]) == 1
    assert payload["achievements"][0]["evidence"][0]["quote"] == "Shipped the search rewrite."

    markdown = report.career_md_path.read_text(encoding="utf-8")
    assert "Search rewrite" in markdown
    assert "> Shipped the search rewrite." in markdown
    assert "[^1]" in markdown


def test_reingest_same_resume_is_idempotent(workspace: Config, tmp_path: Path) -> None:
    resume = _resume_file(tmp_path)
    provider = RecordedProvider(RESPONSE)
    with Storage(workspace.db_path) as storage:
        first = ingest_resume(resume, workspace, storage, provider)
        second = ingest_resume(resume, workspace, storage, provider)
        assert second.source_record_id == first.source_record_id
        assert second.source_reused is True
        assert second.accepted == 0
        assert second.skipped_duplicates == 2
        assert storage.count_profile_items() == 2


def test_conflicting_value_is_preserved_and_surfaced(workspace: Config, tmp_path: Path) -> None:
    conflicting = json.loads(RESPONSE)
    conflicting["items"][1]["detail"] = "10 years of experience"
    v2_dir = tmp_path / "v2"
    v2_dir.mkdir()
    with Storage(workspace.db_path) as storage:
        ingest_resume(_resume_file(tmp_path), workspace, storage, RecordedProvider(RESPONSE))
        report = ingest_resume(
            _resume_file(v2_dir, text=RESUME + "\nMore Python.\n"),
            workspace,
            storage,
            RecordedProvider(json.dumps(conflicting)),
        )
        # identical achievement from a new source merges evidence; conflicting
        # skill is stored side by side
        assert report.conflicts == 1
        assert report.evidence_merged == 1
        assert storage.count_profile_items() == 3
    markdown = report.career_md_path.read_text(encoding="utf-8")
    assert "Conflicts (need your resolution)" in markdown


def test_same_value_new_evidence_is_merged_not_dropped(workspace: Config, tmp_path: Path) -> None:
    second_resume = "# Jo again\n\n- Shipped the search rewrite.\n\nExpert in Python daily.\n"
    second_response = json.loads(RESPONSE)
    second_response["items"] = [
        {
            "kind": "skill",
            "name": "Python",
            "detail": "",
            "classification": "fact",
            "confidence": 0.95,
            "quotes": ["Expert in Python daily."],
        }
    ]
    v2_dir = tmp_path / "v2"
    v2_dir.mkdir()
    with Storage(workspace.db_path) as storage:
        ingest_resume(_resume_file(tmp_path), workspace, storage, RecordedProvider(RESPONSE))
        report = ingest_resume(
            _resume_file(v2_dir, text=second_resume),
            workspace,
            storage,
            RecordedProvider(json.dumps(second_response)),
        )
        assert report.evidence_merged == 1
        assert report.accepted == 0
        assert storage.count_profile_items() == 2  # no duplicate item created
        python_items = [i for i in storage.list_profile_items() if i.name == "Python"]
        assert len(python_items) == 1
        assert {span.quote for span in python_items[0].evidence} == {
            "Skills: Python.",
            "Expert in Python daily.",
        }


def test_empty_resume_fails_visibly(workspace: Config, tmp_path: Path) -> None:
    empty = tmp_path / "empty.md"
    empty.write_text("   \n", encoding="utf-8")
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="empty"):
            ingest_resume(empty, workspace, storage, RecordedProvider(RESPONSE))
        assert storage.count_source_records() == 0


def test_resume_copied_into_inbox(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        ingest_resume(_resume_file(tmp_path), workspace, storage, RecordedProvider(RESPONSE))
        record = storage.get_source_record_by_hash(hashlib.sha256(RESUME.encode()).hexdigest())
    assert record is not None
    assert record.source_locator.startswith("inbox/")
    assert (workspace.data_dir / record.source_locator).read_text(encoding="utf-8") == RESUME


def test_ingest_from_google_url_archives_then_extracts(workspace: Config) -> None:
    from wingman.application.ingest import ingest_resume_from_url

    def fetcher(url: str) -> bytes:
        assert url == "https://docs.google.com/document/d/DOC1/export?format=txt"
        return RESUME.encode()

    with Storage(workspace.db_path) as storage:
        report = ingest_resume_from_url(
            "https://docs.google.com/document/d/DOC1/edit",
            workspace,
            storage,
            RecordedProvider(RESPONSE),
            fetcher=fetcher,
        )
        assert report.accepted == 2
    # the fetched artifact was archived in the inbox before extraction
    archived = list(workspace.inbox_dir.glob("*google-DOC1*"))
    assert len(archived) == 1 and archived[0].suffix == ".txt"
    assert archived[0].read_text(encoding="utf-8") == RESUME
