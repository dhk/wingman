"""Recent-news snapshots: one public RSS GET, replace-on-refresh, visible failures."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.news import STALE_AFTER_DAYS, build_news_query, fetch_person_news
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


FIELD_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>Supersimple Raises $2.2 Million to Rethink How Companies Work With Data in Age of Artificial Intelligence - PR Newswire</title>
    <link>https://news.google.com/rss/articles/real1</link>
    <pubDate>Tue, 02 Apr 2024 10:00:00 GMT</pubDate></item>
  <item><title>This Supersimple Trick Will Make Your Hair Look Great in Photos - Allure</title>
    <link>https://news.google.com/rss/articles/junk1</link>
    <pubDate>Tue, 20 Oct 2015 10:00:00 GMT</pubDate></item>
  <item><title>Here comes Caitie - The Chatham Voice</title>
    <link>https://news.google.com/rss/articles/junk2</link>
    <pubDate>Fri, 29 Aug 2025 10:00:00 GMT</pubDate></item>
  <item><title>4 Marinades to Keep in Your Dinner Arsenal - Oprah.com</title>
    <link>https://news.google.com/rss/articles/junk3</link></item>
  <item><title>My Cats Are Obsessed With This Supersimple Toy: Decorative Peacock Feathers! - Popsugar</title>
    <link>https://news.google.com/rss/articles/junk4</link></item>
  <item><title>Future Today Chosen to Expand Super Simple Songs Beyond YouTube - GlobeNewswire</title>
    <link>https://news.google.com/rss/articles/junk5</link></item>
  <item><title>Marko Klopets on the future of BI - Podcast</title>
    <link>https://news.google.com/rss/articles/real2</link>
    <pubDate>Mon, 13 Jul 2026 10:00:00 GMT</pubDate></item>
</channel></rss>
"""


def test_relevance_filter_on_field_data(workspace: Path) -> None:
    """Regression from the first live run: 'Supersimple' is a common adjective."""
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Marko Klopets", storage, company="Supersimple")
        report = fetch_person_news(
            person, storage, fetcher=lambda url: FIELD_RSS, now=datetime(2026, 7, 20, tzinfo=UTC)
        )
        # relevance keeps the funding story and the person-name headline;
        # every adjective/'Super Simple Songs' item dropped (issue #97 era).
        # The funding story is itself from 2024 (issue #142) so it's then
        # dropped for staleness — only the genuinely recent item survives.
        assert report.stored == 1 and report.dropped == 5 and report.dropped_stale == 1
        assert report.titles == ["Marko Klopets on the future of BI - Podcast"]
        stored = storage.list_person_news(person.person_id)
        assert len(stored) == 1


def test_staleness_cutoff_boundary(workspace: Path) -> None:
    now = datetime(2026, 7, 20, tzinfo=UTC)
    just_inside = now - timedelta(days=STALE_AFTER_DAYS - 1)
    just_outside = now - timedelta(days=STALE_AFTER_DAYS + 1)
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>Marko Klopets: still inside the window</title>
    <link>https://news.google.com/rss/articles/inside</link>
    <pubDate>{just_inside.strftime("%a, %d %b %Y %H:%M:%S GMT")}</pubDate></item>
  <item><title>Marko Klopets: just outside the window</title>
    <link>https://news.google.com/rss/articles/outside</link>
    <pubDate>{just_outside.strftime("%a, %d %b %Y %H:%M:%S GMT")}</pubDate></item>
</channel></rss>
""".encode()
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Marko Klopets", storage)
        report = fetch_person_news(person, storage, fetcher=lambda url: rss, now=now)
        assert report.stored == 1 and report.dropped_stale == 1
        assert report.titles == ["Marko Klopets: still inside the window"]


def test_undated_item_is_kept_not_treated_as_stale(workspace: Path) -> None:
    """We can't verify an undated item's age — guessing 'old' is no more honest than 'new'."""
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>Marko Klopets, no date on this one</title>
    <link>https://news.google.com/rss/articles/undated</link></item>
</channel></rss>
"""
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Marko Klopets", storage)
        report = fetch_person_news(person, storage, fetcher=lambda url: rss)
        assert report.stored == 1 and report.dropped_stale == 0
        assert storage.list_person_news(person.person_id)[0].published_at is None


def test_naive_pubdate_does_not_crash_the_staleness_comparison(workspace: Path) -> None:
    """Regression: a pubDate with no timezone token parses naive; comparing it against
    the (aware) staleness cutoff used to raise TypeError instead of filtering cleanly."""
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>Marko Klopets, naive timestamp</title>
    <link>https://news.google.com/rss/articles/naive</link>
    <pubDate>Mon, 01 Jan 2024 00:00:00</pubDate></item>
</channel></rss>
"""
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Marko Klopets", storage)
        report = fetch_person_news(
            person, storage, fetcher=lambda url: rss, now=datetime(2026, 7, 20, tzinfo=UTC)
        )
        assert report.stored == 0 and report.dropped_stale == 1


def test_all_junk_stores_nothing_but_reports_it(workspace: Path) -> None:
    junk = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>This Supersimple Trick - Allure</title>
    <link>https://news.google.com/rss/articles/j1</link></item>
</channel></rss>
"""
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Marko Klopets", storage, company="Supersimple")
        report = fetch_person_news(person, storage, fetcher=lambda url: junk)
        assert report.stored == 0 and report.dropped == 1
        assert storage.list_person_news(person.person_id) == []
