"""Approved-source research (RFC-015): user-named pages, deterministic diffs."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.research import (
    add_company_source,
    delete_company,
    extract_page,
    list_company_sources,
    remove_company_source,
    rename_company,
    render_research_report,
    research_company,
)
from wingman.infrastructure.config import load_config
from wingman.infrastructure.fetch import FetchError
from wingman.infrastructure.storage import Storage

PAGE_V1 = b"""<html><head><title>Acme Careers</title>
<script>analytics("nonce-12345");</script></head>
<body><h1>Open roles</h1>
<a href="/jobs/data-engineer">Data Engineer</a>
<a href="https://acme.example.com/jobs/pm#apply">PM</a>
<a href="http://insecure.example.com/x">insecure</a>
<a href="/jobs/data-engineer">Data Engineer (dupe)</a>
</body></html>"""

PAGE_V2 = b"""<html><body><h1>Open roles</h1>
<a href="/jobs/data-engineer">Data Engineer</a>
<a href="https://acme.example.com/jobs/pm">PM</a>
<a href="/jobs/staff-mle">Staff MLE</a>
</body></html>"""


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Storage:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    with Storage(config.db_path) as handle:
        yield handle


def test_extract_page_links_and_text() -> None:
    text, links = extract_page(PAGE_V1, "https://acme.example.com/careers")
    # relative resolved, fragment stripped, http and duplicates dropped
    assert links == [
        "https://acme.example.com/jobs/data-engineer",
        "https://acme.example.com/jobs/pm",
    ]
    assert "Open roles" in text and "Data Engineer" in text
    assert "nonce-12345" not in text  # script content is not visible text


def test_add_list_remove_sources(storage: Storage) -> None:
    source, created = add_company_source(
        "Acme Corp", "https://acme.example.com/careers", storage, label="careers"
    )
    assert created and source.company_key == "acme corp"
    _, again = add_company_source("ACME  Corp", "https://acme.example.com/careers", storage)
    assert not again  # same normalized company + url is one approval
    assert [s.url for s in list_company_sources("acme corp", storage)] == [
        "https://acme.example.com/careers"
    ]
    assert remove_company_source("Acme Corp", "https://acme.example.com/careers", storage)
    assert list_company_sources("Acme Corp", storage) == []
    assert not remove_company_source("Acme Corp", "https://acme.example.com/careers", storage)


def test_add_source_rejects_non_https(storage: Storage) -> None:
    with pytest.raises(IngestError, match="https"):
        add_company_source("Acme", "http://acme.example.com/careers", storage)
    with pytest.raises(IngestError, match="empty"):
        add_company_source("   ", "https://acme.example.com/careers", storage)


def test_research_requires_approved_sources(storage: Storage) -> None:
    with pytest.raises(IngestError, match="add-source"):
        research_company("Acme", storage)


def test_research_diffs_snapshots(storage: Storage) -> None:
    add_company_source("Acme", "https://acme.example.com/careers", storage, label="careers")

    first = research_company("Acme", storage, fetcher=lambda url: PAGE_V1)
    assert first.fetched == 1 and first.failed == 0
    assert "first snapshot: 2 links" in first.results[0].detail
    assert first.results[0].new_links == []

    unchanged = research_company("Acme", storage, fetcher=lambda url: PAGE_V1)
    assert unchanged.results[0].detail.startswith("unchanged since ")

    changed = research_company("Acme", storage, fetcher=lambda url: PAGE_V2)
    assert "1 new link since" in changed.results[0].detail
    assert changed.results[0].new_links == ["https://acme.example.com/jobs/staff-mle"]
    rendered = render_research_report(changed)
    assert "✓ https://acme.example.com/careers (careers)" in rendered
    assert "+ https://acme.example.com/jobs/staff-mle" in rendered


def test_research_failure_keeps_previous_snapshot(storage: Storage) -> None:
    add_company_source("Acme", "https://acme.example.com/careers", storage)
    research_company("Acme", storage, fetcher=lambda url: PAGE_V1)

    def boom(url: str) -> bytes:
        raise FetchError("HTTP Error 429")

    report = research_company("Acme", storage, fetcher=boom)
    assert report.failed == 1 and report.results[0].status == "failed"
    assert "previous snapshot was kept" in report.results[0].detail
    # the kept snapshot still diffs correctly on the next good fetch
    recovered = research_company("Acme", storage, fetcher=lambda url: PAGE_V2)
    assert "1 new link since" in recovered.results[0].detail


def test_dossier_renders_research_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from wingman.application.dossier import build_company_dossier
    from wingman.application.people import add_person

    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    with Storage(config.db_path) as storage:
        add_person("Ana", storage, company="Acme")
        add_company_source("Acme", "https://acme.example.com/careers", storage, label="careers")
        before = build_company_dossier("Acme", config, storage).markdown
        assert "## Research (approved sources)" in before
        assert "no snapshot yet" in before
        research_company("Acme", storage, fetcher=lambda url: PAGE_V1)
        after = build_company_dossier("Acme", config, storage).markdown
        assert "2 links, snapshot" in after
        assert "no approved research sources" not in after  # gap line gone


def test_dossier_accumulates_new_links_across_research_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.application.dossier import build_company_dossier
    from wingman.application.people import add_person

    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    with Storage(config.db_path) as storage:
        add_person("Ana", storage, company="Acme")
        add_company_source("Acme", "https://acme.example.com/careers", storage, label="careers")

        # first fetch is a baseline, not "new" — nothing to accumulate yet
        research_company("Acme", storage, fetcher=lambda url: PAGE_V1)
        first = build_company_dossier("Acme", config, storage).markdown
        assert "## New since last dossier (0)" in first
        assert "none recorded yet" in first

        # a run between dossiers surfaces a new link…
        research_company("Acme", storage, fetcher=lambda url: PAGE_V2)
        second = build_company_dossier("Acme", config, storage).markdown
        assert "## New since last dossier (1)" in second
        assert (
            "https://acme.example.com/jobs/staff-mle (via https://acme.example.com/careers)"
            in second
        )

        # …and once summarized, a dossier without any further research shows none
        third = build_company_dossier("Acme", config, storage).markdown
        assert "## New since last dossier (0)" in third
        assert "none since" in third


def test_dossier_new_links_section_caps_and_orders_by_recency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.application.dossier import MAX_NEW_LINKS_SHOWN, build_company_dossier
    from wingman.application.people import add_person

    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    with Storage(config.db_path) as storage:
        add_person("Ana", storage, company="Acme")
        add_company_source("Acme", "https://acme.example.com/careers", storage)
        key = "acme"
        base_page = b"<html><body></body></html>"
        research_company("Acme", storage, fetcher=lambda url: base_page)

        total = MAX_NEW_LINKS_SHOWN + 3
        for index in range(total):
            storage.record_new_links(
                key,
                "https://acme.example.com/careers",
                [f"https://acme.example.com/jobs/{index}"],
                datetime.now(UTC),
            )

        text = build_company_dossier("Acme", config, storage).markdown
        assert f"## New since last dossier ({total})" in text
        assert "(+3 earlier links not shown)" in text
        # the most recently discovered links are the ones shown
        assert f"https://acme.example.com/jobs/{total - 1}" in text
        assert "https://acme.example.com/jobs/0" not in text


def test_rename_company_moves_sources_and_watchlist(storage: Storage) -> None:
    add_company_source("Synctera", "https://synctera.com/careers", storage, label="careers")
    storage.watchlist_add("overnight", "company", "Synctera")
    moved = rename_company("Synctera", "Synctera Inc.", storage)
    assert moved == (1, 0)
    assert list_company_sources("Synctera", storage) == []
    renamed_sources = list_company_sources("Synctera Inc.", storage)
    assert len(renamed_sources) == 1
    assert renamed_sources[0].company_name == "Synctera Inc."
    assert storage.watchlist_members("overnight") == [("company", "Synctera Inc.")]


def test_rename_company_moves_new_link_history_and_dossier_cursor(storage: Storage) -> None:
    add_company_source("Synctera", "https://synctera.com/careers", storage)
    research_company("Synctera", storage, fetcher=lambda url: PAGE_V1)
    research_company("Synctera", storage, fetcher=lambda url: PAGE_V2)  # 1 new link
    storage.mark_dossier_generated("synctera", datetime(2020, 1, 1, tzinfo=UTC))

    rename_company("Synctera", "Synctera Inc.", storage)

    assert storage.list_new_links_since("synctera", None) == []
    moved_events = storage.list_new_links_since("synctera inc.", None)
    assert len(moved_events) == 1
    # PAGE_V2's new link is relative, resolved against the source URL
    assert moved_events[0].url == "https://synctera.com/jobs/staff-mle"
    assert moved_events[0].source_url == "https://synctera.com/careers"

    assert storage.get_dossier_generated_at("synctera") is None
    assert storage.get_dossier_generated_at("synctera inc.") == datetime(2020, 1, 1, tzinfo=UTC)


def test_delete_company_purges_new_link_history_and_dossier_cursor(storage: Storage) -> None:
    add_company_source("Acme", "https://acme.example.com/careers", storage)
    research_company("Acme", storage, fetcher=lambda url: PAGE_V1)
    research_company("Acme", storage, fetcher=lambda url: PAGE_V2)  # 1 new link
    storage.mark_dossier_generated("acme", datetime.now(UTC))

    delete_company("Acme", storage)

    assert storage.list_new_links_since("acme", None) == []
    assert storage.get_dossier_generated_at("acme") is None


def test_rename_company_same_key_is_rejected(storage: Storage) -> None:
    add_company_source("Acme", "https://acme.example.com/careers", storage)
    with pytest.raises(IngestError, match="nothing to rename"):
        rename_company("Acme", "  ACME  ", storage)


def test_delete_company_removes_sources_snapshots_and_watchlist(storage: Storage) -> None:
    add_company_source("Acme", "https://acme.example.com/careers", storage)
    research_company("Acme", storage, fetcher=lambda url: PAGE_V1)
    storage.watchlist_add("AI Target Companies", "company", "Acme")
    removed = delete_company("Acme", storage)
    assert removed
    assert list_company_sources("Acme", storage) == []
    assert storage.get_research_snapshot("acme", "https://acme.example.com/careers") is None
    assert storage.watchlist_members("AI Target Companies") == []


def test_delete_company_nothing_found_returns_false(storage: Storage) -> None:
    assert delete_company("Nobody Corp", storage) == (False, [])


def test_page_title_extraction() -> None:
    from wingman.application.research import page_title

    assert (
        page_title(b"<html><head><title>Ramp Blog \xe2\x80\x94 Posts</title></head></html>")
        == "Ramp Blog — Posts"
    )
    # whitespace collapsed; only the FIRST title element counts
    assert page_title(b"<html><title>A\n   B</title><title>second</title></html>") == "A B"
    assert page_title(b"<html><body>no title here</body></html>") is None
    assert page_title(b"\x00\xffnot html at all") is None
