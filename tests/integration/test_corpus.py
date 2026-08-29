import zipfile
from pathlib import Path

import pytest

from wingman.application.corpus import add_to_corpus, find_evidence, remove_from_corpus
from wingman.application.ingest import IngestError
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import CorpusSearchError, Storage

ESSAY = (
    "# Migrating billing to streaming\n\n"
    "We cut report latency from 24 hours to 20 minutes using Apache Kafka.\n"
)
README = "# wingman\n\nA local-first career intelligence tool written in Python.\n"


@pytest.fixture
def workspace(tmp_path: Path) -> Config:
    config = load_config(env={ENV_DATA_DIR: str(tmp_path / "ws")})
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def test_add_directory_and_search(workspace: Config, tmp_path: Path) -> None:
    corpus_dir = tmp_path / "writing"
    corpus_dir.mkdir()
    (corpus_dir / "essay.md").write_text(ESSAY, encoding="utf-8")
    (corpus_dir / "readme.md").write_text(README, encoding="utf-8")
    (corpus_dir / "photo.png").write_bytes(b"\x89PNG")

    with Storage(workspace.db_path) as storage:
        report = add_to_corpus(corpus_dir, "writing", workspace, storage)
        assert report.added == 2
        assert report.skipped_unsupported == ["photo.png"]
        assert storage.count_corpus_documents() == 2
        assert storage.count_source_records() == 2

        hits = find_evidence("kafka", storage)
        assert len(hits) == 1
        assert hits[0].document.title == "Migrating billing to streaming"
        assert "Kafka" in hits[0].snippet
        assert hits[0].source_locator.startswith("inbox/")
        raw = (workspace.data_dir / hits[0].source_locator).read_text(encoding="utf-8")
        assert raw == ESSAY


def test_re_add_is_idempotent(workspace: Config, tmp_path: Path) -> None:
    essay = tmp_path / "essay.md"
    essay.write_text(ESSAY, encoding="utf-8")
    with Storage(workspace.db_path) as storage:
        first = add_to_corpus(essay, "writing", workspace, storage)
        second = add_to_corpus(essay, "writing", workspace, storage)
        assert first.added == 1
        assert second.added == 0
        assert second.skipped_duplicates == 1
        assert storage.count_corpus_documents() == 1
        assert storage.count_source_records() == 1


def test_add_substack_style_zip(workspace: Config, tmp_path: Path) -> None:
    """Real Substack exports: body-only HTML fragments plus a posts.csv manifest."""
    archive = tmp_path / "substack-export.zip"
    post = "<div><p>Warm introductions beat cold applications.</p></div>"
    unlisted = "<html><head><title>Not In Manifest</title></head><body><p>Text.</p></body></html>"
    manifest = (
        "post_id,post_date,is_published,title,subtitle\n"
        "123.on-career-leverage,2024-11-05T10:00:00.000Z,true,On Career Leverage,How to ask\n"
    )
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("posts.csv", manifest)
        zf.writestr("posts/123.on-career-leverage.html", post)
        zf.writestr("posts/999.unlisted.html", unlisted)
        zf.writestr("email_list.example.csv", "email\nsubscriber@example.com\n")
    with Storage(workspace.db_path) as storage:
        report = add_to_corpus(archive, "substack_post", workspace, storage)
        # posts.csv is consumed as metadata; the subscriber list stays unsupported
        assert report.added == 2
        assert report.skipped_unsupported == ["email_list.example.csv"]
        hits = find_evidence('"warm introductions"', storage)
    assert len(hits) == 1
    document = hits[0].document
    assert document.title == "On Career Leverage"
    assert document.source_type == "substack_post"
    assert document.published_at is not None
    assert document.published_at.date().isoformat() == "2024-11-05"
    assert "Not In Manifest" in report.titles


def test_headers_only_manifest_is_consumed_and_nested_one_is_not(
    workspace: Config, tmp_path: Path
) -> None:
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("posts.csv", "post_id,post_date,is_published,title,subtitle\n")
        zf.writestr("posts/posts.csv", "not,a,manifest\n")
        zf.writestr("posts/1.hello.html", "<p>Hello there world.</p>")
    with Storage(workspace.db_path) as storage:
        report = add_to_corpus(archive, "substack_post", workspace, storage)
    assert report.added == 1
    # only the nested, non-manifest posts.csv is reported as unsupported
    assert report.skipped_unsupported == ["posts.csv"]


def test_corrupt_manifest_does_not_crash_ingestion(workspace: Config, tmp_path: Path) -> None:
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("posts.csv", b"\xff\xfe\x00 not utf-8")
        zf.writestr("posts/1.hello.html", "<p>Hello there world.</p>")
    with Storage(workspace.db_path) as storage:
        report = add_to_corpus(archive, "substack_post", workspace, storage)
    assert report.added == 1
    assert "posts.csv" in report.skipped_unsupported  # unparseable manifest falls back


def test_missing_path_fails_visibly(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="does not exist"):
            add_to_corpus(tmp_path / "nope.md", "writing", workspace, storage)
        assert storage.count_source_records() == 0


def test_bad_search_query_fails_visibly(workspace: Config, tmp_path: Path) -> None:
    essay = tmp_path / "essay.md"
    essay.write_text(ESSAY, encoding="utf-8")
    with Storage(workspace.db_path) as storage:
        add_to_corpus(essay, "writing", workspace, storage)
        with pytest.raises(CorpusSearchError, match="could not be parsed"):
            find_evidence('"unbalanced', storage)


def test_textless_html_leaves_no_orphaned_source_record(workspace: Config, tmp_path: Path) -> None:
    page = tmp_path / "empty.html"
    page.write_text("<html><head><script>let x = 1;</script></head><body></body></html>")
    with Storage(workspace.db_path) as storage:
        report = add_to_corpus(page, "writing", workspace, storage)
        assert report.added == 0
        assert [f.reason for f in report.failures] == ["no text could be extracted"]
        assert storage.count_source_records() == 0
    assert list(workspace.inbox_dir.iterdir()) == []


def test_empty_file_reported_not_stored(workspace: Config, tmp_path: Path) -> None:
    blank = tmp_path / "blank.md"
    blank.write_text("   \n", encoding="utf-8")
    with Storage(workspace.db_path) as storage:
        report = add_to_corpus(blank, "writing", workspace, storage)
        assert report.added == 0
        assert [f.reason for f in report.failures] == ["file is empty"]
        assert storage.count_source_records() == 0


# RFC-078: the corpus is the user's own writing, and their own writing evolves.


def test_edited_essay_supersedes_the_version_it_replaces(workspace: Config, tmp_path: Path) -> None:
    essay = tmp_path / "essay.md"
    essay.write_text(ESSAY, encoding="utf-8")
    with Storage(workspace.db_path) as storage:
        add_to_corpus(essay, "writing", workspace, storage)
        essay.write_text(
            ESSAY.replace("Apache Kafka", "Redpanda").replace("24 hours", "36 hours"),
            encoding="utf-8",
        )
        report = add_to_corpus(essay, "writing", workspace, storage)

        assert report.added == 1
        assert report.replaced == 1
        # One document in the pool, both records kept: the ingests both happened.
        assert storage.count_corpus_documents() == 1
        assert storage.count_source_records() == 2
        # The superseded wording is no longer quotable...
        assert find_evidence("kafka", storage) == []
        # ...and the current one is.
        hits = find_evidence("redpanda", storage)
        assert len(hits) == 1
        assert "36 hours" in hits[0].snippet
    # The archived bytes of both versions stay on disk.
    assert len(list(workspace.inbox_dir.iterdir())) == 2


def test_one_add_never_supersedes_its_own_siblings(workspace: Config, tmp_path: Path) -> None:
    """Two files sharing a basename in one tree are two documents, not one lineage."""
    root = tmp_path / "writing"
    (root / "billing").mkdir(parents=True)
    (root / "wingman").mkdir(parents=True)
    (root / "billing" / "README.md").write_text(ESSAY, encoding="utf-8")
    (root / "wingman" / "README.md").write_text(README, encoding="utf-8")
    with Storage(workspace.db_path) as storage:
        report = add_to_corpus(root, "writing", workspace, storage)
        assert (report.added, report.replaced) == (2, 0)
        assert storage.count_corpus_documents() == 2
        assert len(find_evidence("kafka", storage)) == 1
        assert len(find_evidence("python", storage)) == 1


def test_remove_takes_a_document_out_of_the_pool_and_keeps_the_record(
    workspace: Config, tmp_path: Path
) -> None:
    essay = tmp_path / "essay.md"
    essay.write_text(ESSAY, encoding="utf-8")
    with Storage(workspace.db_path) as storage:
        add_to_corpus(essay, "writing", workspace, storage)
        document = storage.list_corpus_documents()[0]
        storage.upsert_embedding(document.doc_id, "corpus", "voyage", "v1", [0.1, 0.2])

        removed = remove_from_corpus(document.doc_id[:8], storage)

        assert removed.doc_id == document.doc_id
        assert storage.count_corpus_documents() == 0
        assert find_evidence("kafka", storage) == []
        assert storage.get_embedding(document.doc_id) is None
        # Insert-only provenance: the record and the archived file survive.
        assert storage.count_source_records() == 1
        record = storage.get_source_record(document.source_record_id)
        assert record is not None
        assert (workspace.data_dir / record.source_locator).read_text(encoding="utf-8") == ESSAY


def test_remove_reports_an_unknown_or_ambiguous_id(workspace: Config, tmp_path: Path) -> None:
    essay = tmp_path / "essay.md"
    essay.write_text(ESSAY, encoding="utf-8")
    with Storage(workspace.db_path) as storage:
        add_to_corpus(essay, "writing", workspace, storage)
        with pytest.raises(IngestError, match="no corpus document"):
            remove_from_corpus("deadbeef", storage)
        with pytest.raises(IngestError, match="document id is empty"):
            remove_from_corpus("  ", storage)


def test_pre_rfc078_records_gain_lineage_on_open(workspace: Config, tmp_path: Path) -> None:
    """A corpus ingested before this change is still supersedable afterwards."""
    essay = tmp_path / "essay.md"
    essay.write_text(ESSAY, encoding="utf-8")
    with Storage(workspace.db_path) as storage:
        add_to_corpus(essay, "writing", workspace, storage)
        record_id = storage.list_corpus_documents()[0].source_record_id
        # Put the record back the way `corpus add` used to leave it.
        storage._conn.execute(  # noqa: SLF001 — simulating an older database
            "UPDATE source_records SET document_key = '' WHERE record_id = ?", (record_id,)
        )
        storage._conn.commit()  # noqa: SLF001

    with Storage(workspace.db_path) as storage:
        assert storage.get_source_record(record_id).document_key == "essay.md"
        essay.write_text(ESSAY.replace("Apache Kafka", "Redpanda"), encoding="utf-8")
        report = add_to_corpus(essay, "writing", workspace, storage)
        assert (report.added, report.replaced) == (1, 1)
        assert storage.count_corpus_documents() == 1
