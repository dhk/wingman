"""Hosted imports operate on supplied bytes, never client-supplied server paths."""

import asyncio
import base64
import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman import mcp_server as mcp
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


def test_linkedin_profile_import_is_not_connections_import(workspace):
    result = mcp.ingest_linkedin(
        archive(
            {
                "Skills.csv": "Name\nSynthetic skill\n",
                "Connections.csv": "First Name,Last Name,Email Address\nPrivate,Contact,private@example.test\n",
            }
        ),
        "linkedin.zip",
    )
    assert "Accepted: 1" in result
    with Storage(workspace.db_path) as storage:
        assert len(storage.list_profile_items()) == 1
        assert storage.list_people() == []
    assert str(workspace.data_dir) not in result


def test_corpus_payload_import_list_and_duplicate(workspace):
    add = mcp.corpus_add
    data = archive({"../essay.md": "# Synthetic essay\nEvidence from my writing."})
    assert "Added: 1" in add(data, "writing.zip", "substack_post")
    assert "Duplicates skipped: 1" in add(data, "writing.zip", "substack_post")
    result = mcp.corpus_list()
    assert "Synthetic essay" in result and "substack_post" in result
    assert str(workspace.data_dir) not in result
    assert not (workspace.data_dir.parent / "essay.md").exists()


def test_transcript_preview_writes_nothing(workspace):
    before = sorted(workspace.inbox_dir.iterdir())
    result = mcp.ingest_transcript(
        payload(b"# Transcript\n\n00:00:00\n\nAlex: Synthetic conversation."), "call.txt"
    )
    assert "Nothing was written" in result
    assert sorted(workspace.inbox_dir.iterdir()) == before


def test_resume_docx_uses_existing_pipeline(workspace, monkeypatch):
    seen = []

    def ingest(path, config, storage, provider):
        from wingman.application.resume_formats import extract_resume_text

        seen.append(extract_resume_text(path))
        raise mcp.IngestError("synthetic failure with private path " + str(path))

    monkeypatch.setattr(mcp, "ingest_resume", ingest)
    monkeypatch.setattr(mcp, "get_provider", lambda *args: object())
    data = archive(
        {
            "word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Synthetic resume</w:t></w:r></w:p></w:body></w:document>'
        }
    )
    result = mcp.ingest_resume_file(data, "../../resume.docx")
    assert seen and "Synthetic resume" in seen[0]
    assert str(workspace.data_dir) not in result
    assert result.startswith("Nothing was ingested:")
    assert "resume.docx" in result
    # IngestError is a content rejection raised before anything is persisted,
    # so the upload is not kept (retries must not pile up private copies).
    assert list(workspace.inbox_dir.iterdir()) == []


def test_opportunities_cli_matches_mcp(workspace):
    result = CliRunner().invoke(app, ["opportunities", "list"])
    assert result.exit_code == 0
    assert result.stdout.strip() == mcp.opportunities_list()
    assert CliRunner().invoke(app, ["opportunities", "list", "--help"]).exit_code == 0


def test_new_imports_registered_with_payload_schema(workspace):
    tools = {tool.name: tool for tool in asyncio.run(mcp.server.list_tools())}
    for name in ("ingest_resume_file", "ingest_linkedin", "ingest_transcript", "corpus_add"):
        assert name in tools
        assert "content_base64" in tools[name].inputSchema["properties"]
        assert not any("path" in key for key in tools[name].inputSchema["properties"])


@pytest.mark.parametrize("filename", ["resume.md", "resume.docx"])
def test_resume_payload_records_verbatim_evidence_and_lineage(workspace, filename):
    fixtures = Path(__file__).resolve().parents[2] / "fixtures/profile_extraction/case_001_basic"
    workspace.models_config_path.write_text(
        f'[models.extract_fast]\nprovider = "recorded"\npath = "{fixtures / "response.json"}"\n'
    )
    raw = (fixtures / "resume.md").read_bytes()
    data = payload(raw)
    if filename.endswith(".docx"):
        from xml.sax.saxutils import escape

        from wingman.application.resume_formats import extract_resume_text

        text = extract_resume_text(fixtures / "resume.md")
        data = archive(
            {
                "word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
                + escape(text)
                + "</w:t></w:r></w:p></w:body></w:document>"
            }
        )
    result = mcp.ingest_resume_file(data, filename)
    assert "Accepted: 4" in result
    with Storage(workspace.db_path) as storage:
        items = storage.list_profile_items()
        assert len(items) == 4
        for item in items:
            for evidence in item.evidence:
                source = storage.get_source_record(evidence.source_record_id)
                assert source.document_key == filename
                from wingman.application.resume_formats import extract_resume_text

                assert evidence.quote in extract_resume_text(
                    workspace.data_dir / source.source_locator
                )
    assert "Duplicates skipped: 4" in mcp.ingest_resume_file(data, filename)


def test_transcript_files_only_explicit_named_person(workspace):
    from wingman.application.people import add_person

    with Storage(workspace.db_path) as storage:
        alex, _ = add_person("Alex Sample", storage)
        blair, _ = add_person("Blair Sample", storage)
    data = payload(b"# Transcript\n\n00:00:00\n\nAlex: Synthetic conversation.")
    assert "Nothing was filed" in mcp.ingest_transcript(data, "call.txt", "Nobody")
    assert "Filed against Blair Sample" in mcp.ingest_transcript(
        data,
        "call.txt",
        "Blair Sample",
        reviewed_hash=hashlib.sha256(base64.b64decode(data)).hexdigest(),
    )
    with Storage(workspace.db_path) as storage:
        assert storage.list_log_entries(alex.person_id) == []
        entries = storage.list_log_entries(blair.person_id)
        assert len(entries) == 1 and "transcribed by Gemini" in entries[0].note
        assert storage.list_profile_items() == []


@pytest.mark.parametrize(
    "name,data",
    [
        ("resume.txt", "!invalid!"),
        ("resume.txt", ""),
        ("resume.exe", payload(b"text")),
        ("bad\nname.txt", payload(b"text")),
        ("broken.docx", payload(b"not a zip")),
    ],
)
def test_invalid_payloads_do_not_write(workspace, name, data):
    result = mcp.ingest_resume_file(data, name)
    assert result.startswith("Nothing was ingested:")
    assert list(workspace.inbox_dir.iterdir()) == []


def test_shared_corpus_is_tenant_scoped(workspace, monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp, "_ready_config", lambda: workspace.model_copy(update={"privileged": False})
    )
    assert "Added: 1" in mcp.corpus_add(payload(b"# Private synthetic corpus"), "../../note.md")
    other = load_config(env={ENV_DATA_DIR: str(tmp_path / "other")})
    other.data_dir.mkdir()
    Storage(other.db_path).close()
    monkeypatch.setattr(
        mcp, "_ready_config", lambda: other.model_copy(update={"privileged": False})
    )
    assert "Corpus is empty" in mcp.corpus_list()
    assert not (tmp_path / "note.md").exists()


def test_registered_mcp_call_imports_real_payload(workspace):
    async def invoke():
        return await mcp.server.call_tool(
            "corpus_add",
            {
                "content_base64": payload(b"# Through MCP\nSynthetic evidence."),
                "filename": "essay.md",
            },
        )

    result = asyncio.run(invoke())
    assert "Added: 1" in str(result)
    assert "Through MCP" in mcp.corpus_list()


def test_registered_file_payload_is_not_duplicated_into_telemetry(workspace, monkeypatch):
    captured = []
    monkeypatch.setattr(mcp, "record_event", lambda *args, **kwargs: captured.append(args[3]))
    data = payload(b"# Private synthetic text\nNever journal the upload bytes.")
    asyncio.run(mcp.server.call_tool("corpus_add", {"content_base64": data, "filename": "note.md"}))
    assert captured
    assert data not in str(captured)


def test_a_linkedin_zip_with_nothing_importable_is_not_kept(workspace):
    """Content rejected after archiving must not leave a private copy behind,
    and the reply names the real problem rather than storage or the model."""
    result = mcp.ingest_linkedin(archive({"readme.txt": "not an export"}), "linkedin.zip")
    assert result.startswith("Nothing was imported:")
    assert "LinkedIn data export" in result
    assert "model availability" not in result
    assert list(workspace.inbox_dir.iterdir()) == []


def test_a_corpus_file_that_adds_nothing_is_not_kept_or_named_internally(workspace):
    result = mcp.corpus_add(payload(b"\xff\xfe not utf-8 \xff"), "essay.txt")
    assert "Added: 0" in result
    assert "failed 'essay.txt'" in result
    assert "the upload was not kept" in result
    assert list(workspace.inbox_dir.iterdir()) == []


def test_a_resume_with_no_extractable_text_is_not_kept(workspace, monkeypatch):
    class NoModel:
        def complete(self, request):
            raise AssertionError("model called for a rejected file")

    monkeypatch.setattr(mcp, "get_provider", lambda *args: NoModel())
    result = mcp.ingest_resume_file(payload(b"   \n  "), "resume.txt")
    assert result.startswith("Nothing was ingested:")
    assert "resume.txt" in result
    assert list(workspace.inbox_dir.iterdir()) == []


def test_transcript_filing_requires_the_full_name(workspace):
    """A unique partial match is an inferred recipient; the tool contract forbids it."""
    from wingman.application.people import add_person

    with Storage(workspace.db_path) as storage:
        sam, _ = add_person("Sam Sample", storage)
    data = payload(b"# Transcript\n\n00:00:00\n\nSam: Synthetic conversation.")
    result = mcp.ingest_transcript(
        data,
        "call.txt",
        "Sam",
        reviewed_hash=hashlib.sha256(base64.b64decode(data)).hexdigest(),
    )
    assert "Nothing was filed" in result
    assert "'Sam Sample'" in result
    with Storage(workspace.db_path) as storage:
        assert storage.list_log_entries(sam.person_id) == []
    assert "Filed against Sam Sample" in mcp.ingest_transcript(
        data,
        "call.txt",
        "sam  sample",
        reviewed_hash=hashlib.sha256(base64.b64decode(data)).hexdigest(),
    )


def test_transcript_filing_requires_matching_review_hash(workspace):
    from wingman.application.people import add_person

    with Storage(workspace.db_path) as storage:
        person, _ = add_person("Robin Example", storage)
    raw = b"# Transcript\nRobin: Synthetic conversation."
    data = payload(raw)
    result = mcp.ingest_transcript(data, "call.txt", "Robin Example")
    assert "Nothing was filed" in result
    assert "reviewed_hash" in result
    assert list(workspace.inbox_dir.iterdir()) == []
    assert "Nothing was filed" in mcp.ingest_transcript(
        data, "call.txt", "Robin Example", reviewed_hash="0" * 64
    )
    preview = mcp.ingest_transcript(data, "call.txt")
    assert hashlib.sha256(raw).hexdigest() in preview
    with Storage(workspace.db_path) as storage:
        assert storage.list_log_entries(person.person_id) == []


def test_filed_transcript_is_endorsed_and_preserves_original(workspace):

    from wingman.application.people import add_person
    from wingman.domain.relationship import EvidenceTier

    with Storage(workspace.db_path) as storage:
        person, _ = add_person("Robin Example", storage)
    raw = (
        b"## Next steps\n- Robin will send an offer.\n# Transcript\nRobin: Synthetic conversation."
    )
    kwargs = {"reviewed_hash": hashlib.sha256(raw).hexdigest()}
    result = mcp.ingest_transcript(payload(raw), "call.txt", "Robin Example", **kwargs)
    assert "Filed against Robin Example" in result
    with Storage(workspace.db_path) as storage:
        [entry] = storage.list_log_entries(person.person_id)
        assert entry.evidence_tier is EvidenceTier.ENDORSED
        original = workspace.data_dir / entry.source_url
        assert original.read_bytes() == raw
        assert storage.get_source_record_by_hash(hashlib.sha256(raw).hexdigest()) is not None
    assert "[endorsed]" in result


def test_file_import_journal_withholds_names_results_and_corpus_titles(workspace, monkeypatch):
    captured = []
    monkeypatch.setattr(mcp, "record_event", lambda *args, **kwargs: captured.append(args[3]))
    asyncio.run(
        mcp.server.call_tool(
            "corpus_add",
            {
                "content_base64": payload(b"# Confidential synthetic title\nPrivate prose."),
                "filename": "private-client-name.md",
                "source_type": "private-label",
            },
        )
    )
    asyncio.run(mcp.server.call_tool("corpus_list", {}))
    journal = str(captured)
    for private in (
        "Confidential synthetic title",
        "private-client-name",
        "private-label",
        "Private prose",
    ):
        assert private not in journal


def test_unexpected_import_error_is_redacted_at_tool_boundary(workspace, monkeypatch):
    def broken(*args):
        raise RuntimeError("private exception content /secret/path")

    monkeypatch.setattr("wingman.application.corpus.add_to_corpus", broken)
    result = asyncio.run(
        mcp.server.call_tool(
            "corpus_add",
            {
                "content_base64": payload(b"# Synthetic essay"),
                "filename": "essay.md",
            },
        )
    )
    assert "private exception content" not in str(result)
    assert "/secret/path" not in str(result)
    assert "Import failed" in str(result)
