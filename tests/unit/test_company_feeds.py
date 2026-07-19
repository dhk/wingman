"""Company-attached feeds (RFC-029): follow a company blog with no person in the loop."""

from pathlib import Path

import pytest

from wingman.application.company_feeds import (
    attach_company_feed,
    ensure_company_anchor,
    fetch_company_feeds,
    get_company_anchor,
    is_company_anchor,
    list_company_feeds,
    remove_company_feed,
)
from wingman.application.ingest import IngestError
from wingman.application.people import add_person, delete_person, match_people
from wingman.application.research import delete_company, rename_company
from wingman.domain.person import FeedAttribution
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<title>Cursor Blog</title>
<item><title>Shipping fast</title><link>https://cursor.example.com/blog/fast</link>
<description>How we ship.</description><pubDate>Wed, 15 Jul 2026 10:00:00 GMT</pubDate></item>
<item><title>Model routing</title><link>https://cursor.example.com/blog/routing</link>
<description>Routing details.</description><pubDate>Thu, 16 Jul 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def test_attach_list_remove_no_person_needed(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        anchor, source = attach_company_feed(
            "Cursor", "https://cursor.example.com/blog/rss/", storage
        )
        assert is_company_anchor(anchor)
        assert anchor.person_id == "__company__cursor"
        assert source.attribution is FeedAttribution.ORGANIZATION
        assert source.org_name == "Cursor"
        assert source.url == "https://cursor.example.com/blog/rss"  # normalized
        assert [feed.url for feed in list_company_feeds("Cursor", storage)] == [
            "https://cursor.example.com/blog/rss"
        ]
        # duplicate rejected, same as person feeds
        with pytest.raises(IngestError, match="already has"):
            attach_company_feed("Cursor", "https://cursor.example.com/blog/rss", storage)
        assert remove_company_feed("Cursor", "https://cursor.example.com/blog/rss/", storage)
        assert list_company_feeds("Cursor", storage) == []
        assert not remove_company_feed("Cursor", "https://cursor.example.com/blog/rss", storage)


def test_fetch_flows_into_dossier(workspace: Config) -> None:
    from wingman.application.dossier import build_company_dossier

    with Storage(workspace.db_path) as storage:
        attach_company_feed("Cursor", "https://cursor.example.com/blog/rss", storage)
        report = fetch_company_feeds("Cursor", workspace, storage, fetcher=lambda url: RSS)
        assert report.added == 2
        dossier = build_company_dossier("Cursor", workspace, storage).markdown
        # Org-attributed posts carry the dossier with zero people involved:
        assert "Newest attributable document: 2026-07-16" in dossier
        assert "## Organization sources" in dossier
        assert "https://cursor.example.com/blog/rss" in dossier
        assert "none — documents come from org-attributed feeds only" in dossier


def test_anchor_hidden_from_people_and_guarded(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        attach_company_feed("Cursor", "https://cursor.example.com/blog/rss", storage)
        add_person("Ana", storage)
        visible = [p.name for p in storage.list_people() if not is_company_anchor(p)]
        assert visible == ["Ana"]
        # people-surface mutations refuse the anchor
        with pytest.raises(IngestError, match="company feed anchor"):
            delete_person("Cursor (company)", storage)
        assert match_people(storage, "Cursor (company)")  # still findable internally


def test_rename_company_carries_anchor_and_posts(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        attach_company_feed("Cursor", "https://cursor.example.com/blog/rss", storage)
        fetch_company_feeds("Cursor", workspace, storage, fetcher=lambda url: RSS)
        rename_company("Cursor", "Cursor Inc", storage)
        assert get_company_anchor("Cursor", storage) is None
        moved = get_company_anchor("Cursor Inc", storage)
        assert moved is not None and moved.company == "Cursor Inc"
        assert [feed.url for feed in moved.sources] == ["https://cursor.example.com/blog/rss"]
        docs = storage.list_external_documents(moved.person_id)
        assert {doc.title for doc in docs} == {"Shipping fast", "Model routing"}


def test_delete_company_removes_anchor_and_posts(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        attach_company_feed("Cursor", "https://cursor.example.com/blog/rss", storage)
        fetch_company_feeds("Cursor", workspace, storage, fetcher=lambda url: RSS)
        removed, _ = delete_company("Cursor", storage)
        assert removed
        assert get_company_anchor("Cursor", storage) is None
        assert storage.list_external_documents("__company__cursor") == []


def test_fetch_without_feeds_fails_visibly(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="no company feeds"):
            fetch_company_feeds("Cursor", workspace, storage)
        ensure_company_anchor("Nimbus", storage)
        with pytest.raises(IngestError, match="no company feeds"):
            fetch_company_feeds("Nimbus", workspace, storage)


def test_mcp_company_feed_roundtrip(workspace: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    import wingman.application.people as people_module
    from wingman.mcp_server import company_feed

    Storage(workspace.db_path).close()  # _ready_config requires an initialized workspace
    monkeypatch.setattr(people_module, "fetch_url", lambda url: RSS)
    assert "Attached to Cursor" in company_feed(
        "attach", "Cursor", "https://cursor.example.com/blog/rss"
    )
    assert "cursor.example.com" in company_feed("list", "Cursor")
    fetched = company_feed("fetch", "Cursor")
    assert "2 added" in fetched and "Shipping fast" in fetched
    assert "Removed" in company_feed("remove", "Cursor", "https://cursor.example.com/blog/rss")
    assert "unknown action" in company_feed("nope", "Cursor")
