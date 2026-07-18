"""Recent-news snapshots: one public RSS GET, replace-on-refresh, visible failures."""

from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.news import build_news_query, fetch_person_news
from wingman.application.people import add_person
from wingman.infrastructure.config import load_config
from wingman.infrastructure.fetch import FetchError
from wingman.infrastructure.storage import Storage

NEWS_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>Supersimple raises round - TechNews</title>
    <link>https://news.google.com/rss/articles/abc</link>
    <pubDate>Mon, 13 Jul 2026 10:00:00 GMT</pubDate></item>
  <item><title>Untitled junk</title><link>http://insecure.example.com/x</link></item>
  <item><title>Marko Klopets on BI - Podcast</title>
    <link>https://news.google.com/rss/articles/def</link></item>
</channel></rss>
"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return tmp_path


def test_news_query_includes_person_and_company(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Marko Klopets", storage, company="Supersimple")
        assert build_news_query(person) == '"Marko Klopets" OR "Supersimple"'
        solo, _ = add_person("No Company", storage)
        assert build_news_query(solo) == '"No Company"'


def test_news_fetch_stores_snapshot_and_replaces_it(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Marko Klopets", storage, company="Supersimple")
        urls: list[str] = []

        def fetcher(url: str) -> bytes:
            urls.append(url)
            return NEWS_RSS

        report = fetch_person_news(person, storage, fetcher=fetcher)
        assert "news.google.com/rss/search" in urls[0]
        assert "%22Marko%20Klopets%22" in urls[0]
        # the non-https item was dropped; two survived
        assert report.stored == 2
        stored = storage.list_person_news(person.person_id)
        assert len(stored) == 2
        assert stored[0].published_at is not None

        # refresh replaces, never accumulates
        fetch_person_news(person, storage, fetcher=fetcher)
        assert len(storage.list_person_news(person.person_id)) == 2


def test_news_fetch_failure_keeps_the_old_snapshot(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Marko Klopets", storage)
        fetch_person_news(person, storage, fetcher=lambda url: NEWS_RSS)
        before = storage.list_person_news(person.person_id)

        def failing(url: str) -> bytes:
            raise FetchError("HTTP Error 429")

        with pytest.raises(IngestError, match="snapshot was kept"):
            fetch_person_news(person, storage, fetcher=failing)
        assert storage.list_person_news(person.person_id) == before
