import zipfile
from pathlib import Path

import pytest

from wingman.application.corpus import add_to_corpus, find_evidence
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
    archive = tmp_path / "substack-export.zip"
    post = (
        "<html><head><title>On Career Leverage</title></head>"
        "<body><p>Warm introductions beat cold applications.</p></body></html>"
    )
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("posts/123.on-career-leverage.html", post)
        zf.writestr("posts/posts.csv", "id,title\n123,On Career Leverage\n")
    with Storage(workspace.db_path) as storage:
        report = add_to_corpus(archive, "substack_post", workspace, storage)
        assert report.added == 1
        assert report.skipped_unsupported == ["posts.csv"]
        hits = find_evidence('"warm introductions"', storage)
    assert len(hits) == 1
    assert hits[0].document.title == "On Career Leverage"
    assert hits[0].document.source_type == "substack_post"


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
