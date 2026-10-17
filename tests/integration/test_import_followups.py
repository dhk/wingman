"""Private uploads are retained once, and endorsement previews are exact."""

import asyncio
import base64
import hashlib
import io
import logging
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman import mcp_server as mcp
from wingman.application.people import add_person
from wingman.cli.main import app
from wingman.infrastructure.config import ENV_DATA_DIR, load_config
from wingman.infrastructure.storage import Storage


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    for path in (config.data_dir, config.inbox_dir, config.reports_dir):
        path.mkdir(parents=True, exist_ok=True)
    Storage(config.db_path).close()
    return config


def payload(raw):
    return base64.b64encode(raw).decode("ascii")


def archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as output:
        for name, content in files.items():
            output.writestr(name, content)
    return payload(stream.getvalue())


RAW = b"## Next steps\n- Robin will send a   draft.\n# Transcript\nRobin: Synthetic conversation."


def test_three_corpus_uploads_retain_no_extra_copies(workspace):
    data = archive({"essay.md": "# Synthetic essay\nPrivate writing."})
    assert "Added: 1" in mcp.corpus_add(data, "writing.zip")
    before = set(workspace.inbox_dir.iterdir())
    for _ in range(2):
        assert "Duplicates skipped: 1" in mcp.corpus_add(data, "writing.zip")
    assert set(workspace.inbox_dir.iterdir()) == before


def test_three_resume_uploads_retain_no_extra_copies(workspace):
    fixtures = Path(__file__).resolve().parents[2] / "fixtures/profile_extraction/case_001_basic"
    workspace.models_config_path.write_text(
        f'[models.extract_fast]\nprovider = "recorded"\npath = "{fixtures / "response.json"}"\n'
    )
    data = payload((fixtures / "resume.md").read_bytes())
    assert "Accepted: 4" in mcp.ingest_resume_file(data, "resume.md")
    before = set(workspace.inbox_dir.iterdir())
    for _ in range(2):
        assert "Duplicates skipped: 4" in mcp.ingest_resume_file(data, "resume.md")
    assert set(workspace.inbox_dir.iterdir()) == before
    with Storage(workspace.db_path) as storage:
        for item in storage.list_profile_items():
            for evidence in item.evidence:
                source = storage.get_source_record(evidence.source_record_id)
                assert (workspace.data_dir / source.source_locator).is_file()


def test_linkedin_info_contains_counts_without_upload_identifiers(workspace, caplog):
    with caplog.at_level(logging.INFO):
        assert "Accepted: 1" in mcp.ingest_linkedin(
            archive({"Skills.csv": "Name\nSynthetic skill\n"}), "confidential-export.zip"
        )
    messages = [r.getMessage() for r in caplog.records if "linkedin_import" in r.getMessage()]
    assert messages and "accepted=1" in messages[-1]
    assert "confidential-export" not in messages[-1]
    assert str(workspace.inbox_dir) not in messages[-1]


def test_refiling_same_bytes_same_person_is_idempotent(workspace):
    with Storage(workspace.db_path) as storage:
        person, _ = add_person("Robin Example", storage)
        other, _ = add_person("Other Example", storage)
    digest = hashlib.sha256(RAW).hexdigest()
    assert "Filed against" in mcp.ingest_transcript(payload(RAW), "call.txt", person.name, digest)
    files = set(workspace.inbox_dir.iterdir())
    with Storage(workspace.db_path) as storage:
        count = storage.count_source_records()
        [entry] = storage.list_log_entries(person.person_id)
    for _ in range(2):
        result = mcp.ingest_transcript(payload(RAW), "renamed.txt", person.name, digest)
        assert f"already filed on {entry.happened_at.date().isoformat()}" in result.lower()
    assert set(workspace.inbox_dir.iterdir()) == files
    with Storage(workspace.db_path) as storage:
        assert storage.count_source_records() == count
        assert len(storage.list_log_entries(person.person_id)) == 1
    assert "Filed against" in mcp.ingest_transcript(payload(RAW), "call.txt", other.name, digest)
    changed = RAW + b"\nRobin: Another turn."
    assert "Filed against" in mcp.ingest_transcript(
        payload(changed), "call.txt", person.name, hashlib.sha256(changed).hexdigest()
    )


def test_transcript_preview_shows_exact_stored_note(workspace):
    with Storage(workspace.db_path) as storage:
        person, _ = add_person("Robin Example", storage)
    preview = mcp.ingest_transcript(payload(RAW), "call.txt")
    marker = "Exact note to file [endorsed]:\n"
    assert marker in preview
    exact_note = preview.split(marker, 1)[1].split("\nReview hash:", 1)[0]
    mcp.ingest_transcript(payload(RAW), "call.txt", person.name, hashlib.sha256(RAW).hexdigest())
    with Storage(workspace.db_path) as storage:
        [entry] = storage.list_log_entries(person.person_id)
        assert exact_note == entry.note


def test_unexpected_exception_journal_does_not_store_exception_text(workspace, monkeypatch):
    captured = []
    monkeypatch.setattr(mcp, "record_event", lambda *a, **kw: captured.append(a[3]))

    def broken(*args):
        raise RuntimeError("synthetic-private-marker /private/synthetic-upload")

    monkeypatch.setattr("wingman.application.corpus.add_to_corpus", broken)
    result = asyncio.run(
        mcp.server.call_tool(
            "corpus_add", {"content_base64": payload(b"# Essay"), "filename": "essay.md"}
        )
    )
    assert "Import failed" in str(result)
    assert captured[-1]["error"] == "RuntimeError"
    assert "synthetic-private-marker" not in str(captured)


def test_corpus_info_does_not_log_absolute_upload_path(workspace, caplog):
    with caplog.at_level(logging.INFO):
        mcp.corpus_add(payload(b"# Essay\nSynthetic prose."), "confidential-essay.md")
    messages = [r.getMessage() for r in caplog.records if "corpus_add" in r.getMessage()]
    assert messages and "added=1" in messages[-1]
    assert str(workspace.inbox_dir) not in messages[-1]
    assert "confidential-essay.md" not in messages[-1]


def test_cli_transcript_refuses_unique_partial_name_without_writes(workspace, tmp_path):
    with Storage(workspace.db_path) as storage:
        add_person("Robin Example", storage)
    path = tmp_path / "call.txt"
    path.write_bytes(RAW)
    before = set(workspace.inbox_dir.iterdir())
    result = CliRunner().invoke(
        app,
        [
            "ingest-transcript",
            str(path),
            "--note-for",
            "Robin",
            "--reviewed-hash",
            hashlib.sha256(RAW).hexdigest(),
        ],
    )
    assert result.exit_code == 1
    assert "name an existing person in full" in result.output
    assert set(workspace.inbox_dir.iterdir()) == before
    with Storage(workspace.db_path) as storage:
        assert storage.list_log_entries() == []


def test_hash_rejection_never_archives_original(workspace, monkeypatch):
    from wingman.application import transcript_import
    from wingman.application.ingest import IngestError

    with Storage(workspace.db_path) as storage:
        add_person("Robin Example", storage)
        transcript, _, _ = transcript_import.preview_transcript(RAW, "call.txt")
        archived = []
        monkeypatch.setattr(transcript_import, "archive_file", lambda *a: archived.append(a))
        with pytest.raises(IngestError, match="preview first"):
            transcript_import.file_transcript(
                RAW, "call.txt", transcript, "Robin Example", "0" * 64, workspace, storage
            )
        assert archived == []
