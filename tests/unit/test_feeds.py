"""RFC-011: general feeds — Atom parsing, discovery, index pages, attribution."""

from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.people import (
    add_person,
    attach_feed,
    discover_feed,
    fetch_person_feed,
    find_people_evidence,
)
from wingman.domain.person import FeedAttribution, FeedKind, FeedSource
from wingman.infrastructure.config import load_config
from wingman.infrastructure.fetch import FetchError
from wingman.infrastructure.storage import Storage

ATOM_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Marko on Data</title>
  <entry>
    <title>Semantic Layers</title>
    <link rel="alternate" href="https://medium.com/@marko/semantic-layers"/>
    <published>2026-07-01T12:00:00Z</published>
    <content type="html">&lt;p&gt;Semantic layers make analytics explainable.&lt;/p&gt;</content>
  </entry>
</feed>
"""

RSS_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Example Letters</title>
<item><title>On Kafka</title><link>https://blog.example.com/on-kafka</link>
<description>&lt;p&gt;Kafka everywhere.&lt;/p&gt;</description></item>
</channel></rss>
"""

HOMEPAGE_WITH_AUTODISCOVERY = b"""<html><head>
<link rel="alternate" type="application/rss+xml" href="/blog/feed.xml"/>
<title>A Blog</title></head><body>hello</body></html>
"""

INDEX_PAGE = b"""<html><body>
<a href="/insights/the-most-important-investment">Post one</a>
<a href="https://www.example-vc.com/insights/unveiling-rdi">Post two</a>
<a href="/insights/the-most-important-investment">duplicate link</a>
<a href="/team/scott">off-path page</a>
<a href="https://elsewhere.com/insights/foreign">other host</a>
</body></html>
"""

POST_PAGE = b"""<html><head><title>The Most Important Investment</title></head>
<body><p>Research driven ideation and the most important investment thinking.</p></body></html>
"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return tmp_path


def test_discover_direct_feed_url() -> None:
    discovery = discover_feed("https://blog.example.com/feed", fetcher=lambda url: RSS_FEED)
    assert discovery.feed_url == "https://blog.example.com/feed"
    assert discovery.feed_title == "Example Letters"


def test_discover_via_html_autodiscovery() -> None:
    def fetch(url: str) -> bytes:
        if url == "https://blog.example.com":
            return HOMEPAGE_WITH_AUTODISCOVERY
        if url == "https://blog.example.com/blog/feed.xml":
            return ATOM_FEED
        raise AssertionError(f"unexpected fetch {url}")

    discovery = discover_feed("https://blog.example.com", fetcher=fetch)
    assert discovery.feed_url == "https://blog.example.com/blog/feed.xml"
    assert discovery.feed_title == "Marko on Data"


def test_discover_via_conventional_paths() -> None:
    def fetch(url: str) -> bytes:
        if url == "https://blog.example.com/rss.xml":
            return RSS_FEED
        return b"<html><head></head><body>no feed link</body></html>"

    discovery = discover_feed("https://blog.example.com", fetcher=fetch)
    assert discovery.feed_url == "https://blog.example.com/rss.xml"


def test_discover_nothing_returns_probes() -> None:
    discovery = discover_feed(
        "https://www.example-vc.com/insights", fetcher=lambda url: b"<html>no feeds</html>"
    )
    assert discovery.feed_url is None
    assert len(discovery.probed) > 1


def test_discover_rejects_http() -> None:
    with pytest.raises(IngestError, match="https"):
        discover_feed("http://blog.example.com", fetcher=lambda url: RSS_FEED)


def test_atom_feed_fetch_with_attach(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Marko Klopets", storage)
        attach_feed(person, FeedSource(url="https://medium.com/feed/@marko"), storage)
        person = storage.find_person_by_name_key("marko klopets")
        assert person is not None
        report = fetch_person_feed(person, config, storage, fetcher=lambda url: ATOM_FEED)
        assert report.added == 1
        document = storage.list_external_documents(person.person_id)[0]
        assert document.title == "Semantic Layers"
        assert document.source_type == "rss_feed"
        assert document.published_at is not None and document.published_at.year == 2026


def test_one_dead_source_does_not_stop_the_others(workspace: Path) -> None:
    """A 404 on one feed must not abort every other configured source for
    the same person (overnight P2 — Peter Hazlehurst's dead Synctera feed
    stalled fetch/pov/brief for sources that were otherwise fine)."""
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Peter", storage)
        person = attach_feed(person, FeedSource(url="https://dead.example.com/blog/feed"), storage)
        person = attach_feed(person, FeedSource(url="https://live.example.com/feed"), storage)

        def fetch(url: str) -> bytes:
            if url == "https://dead.example.com/blog/feed":
                raise FetchError(f"HTTP 404 fetching {url}")
            return RSS_FEED

        report = fetch_person_feed(person, config, storage, fetcher=fetch)
        assert report.added == 1
        assert len(report.failed_sources) == 1
        assert "dead.example.com" in report.failed_sources[0]
        # the live source's post still got indexed despite the dead one
        assert storage.list_external_documents(person.person_id)


def test_fetch_raises_only_when_every_source_fails(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("No Live Sources", storage)
        person = attach_feed(person, FeedSource(url="https://a.example.com/feed"), storage)
        person = attach_feed(person, FeedSource(url="https://b.example.com/feed"), storage)

        def always_fails(url: str) -> bytes:
            raise FetchError(f"HTTP 404 {url}")

        with pytest.raises(IngestError, match="every source failed"):
            fetch_person_feed(person, config, storage, fetcher=always_fails)


def test_attach_rejects_duplicate_source(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Jane", storage, substack_url="https://jane.substack.com")
        with pytest.raises(IngestError, match="already has"):
            attach_feed(person, FeedSource(url="https://jane.substack.com/feed"), storage)
        # duplicates are caught up to trailing-slash normalization too
        with pytest.raises(IngestError, match="already has"):
            attach_feed(person, FeedSource(url="https://jane.substack.com/feed/"), storage)


def test_attach_enforces_invariants(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Jane", storage)
        with pytest.raises(IngestError, match="https"):
            attach_feed(person, FeedSource(url="http://insecure.example.com/feed"), storage)
        with pytest.raises(IngestError, match="organization name"):
            attach_feed(
                person,
                FeedSource(
                    url="https://firm.example.com/insights",
                    kind=FeedKind.INDEX_PAGE,
                    attribution=FeedAttribution.ORGANIZATION,
                ),
                storage,
            )


def test_custom_domain_substack_is_labeled_substack(workspace: Path) -> None:
    substack = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Custom</title>
<item><title>A Post</title><link>https://www.custom-domain.blog/p/a-post</link>
<description>&lt;p&gt;custom domain content&lt;/p&gt;</description></item></channel></rss>"""
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Custom", storage, substack_url="https://www.custom-domain.blog")
        report = fetch_person_feed(person, config, storage, fetcher=lambda url: substack)
        assert report.added == 1
        document = storage.list_external_documents(person.person_id)[0]
        assert document.source_type == "substack_feed"


def test_index_page_source_with_org_attribution(workspace: Path) -> None:
    config = load_config()
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        if url == "https://www.example-vc.com/insights":
            return INDEX_PAGE
        # distinct content per page, so each post hashes differently
        return POST_PAGE.replace(b"</p>", f" — from {url}</p>".encode())

    with Storage(config.db_path) as storage:
        person, _ = add_person("Scott Brady", storage)
        attach_feed(
            person,
            FeedSource(
                url="https://www.example-vc.com/insights",
                kind=FeedKind.INDEX_PAGE,
                attribution=FeedAttribution.ORGANIZATION,
                org_name="Example VC",
            ),
            storage,
        )
        person = storage.find_person_by_name_key("scott brady")
        assert person is not None
        report = fetch_person_feed(person, config, storage, fetcher=fetch)
        # two on-path posts ingested; off-path, foreign-host, duplicate links skipped
        assert report.added == 2
        assert "https://www.example-vc.com/team/scott" not in fetched
        assert all("elsewhere.com" not in url for url in fetched)
        documents = storage.list_external_documents(person.person_id)
        assert {d.organization for d in documents} == {"Example VC"}
        assert all(d.source_type == "web_page" for d in documents)

        # re-fetch: already-seen URLs are not fetched again
        again = fetch_person_feed(person, config, storage, fetcher=fetch)
        assert again.added == 0

        hits = find_people_evidence("ideation", storage)
        assert hits and hits[0].person_name == "Scott Brady"
        assert hits[0].document.organization == "Example VC"


def test_substack_and_added_feed_both_fetch(workspace: Path) -> None:
    config = load_config()

    substack = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Jane</title>
<item><title>Substack Post</title><link>https://jane.substack.com/p/one</link>
<description>&lt;p&gt;from substack&lt;/p&gt;</description></item></channel></rss>"""

    def fetch(url: str) -> bytes:
        return substack if "substack" in url else ATOM_FEED

    with Storage(config.db_path) as storage:
        person, _ = add_person("Jane", storage, substack_url="https://jane.substack.com")
        attach_feed(person, FeedSource(url="https://medium.com/feed/@jane"), storage)
        person = storage.find_person_by_name_key("jane")
        assert person is not None
        report = fetch_person_feed(person, config, storage, fetcher=fetch)
        assert report.added == 2
        types = {d.source_type for d in storage.list_external_documents(person.person_id)}
        assert types == {"substack_feed", "rss_feed"}


def test_discovery_finds_feed_via_page_anchor() -> None:
    """Webflow pattern: RSS exists, but only an <a> in the footer points at it."""
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        if url == "https://www.example.com/blog":
            return (
                b'<html><body><a href="/blog/latest">Latest</a>'
                b'<a href="/blog/rss.xml">RSS</a></body></html>'
            )
        if url == "https://www.example.com/blog/rss.xml":
            return RSS_FEED
        raise FetchError("404")

    discovery = discover_feed("https://www.example.com/blog", fetcher=fetch)
    assert discovery.feed_url == "https://www.example.com/blog/rss.xml"
    # the non-feed anchor was never fetched
    assert "https://www.example.com/blog/latest" not in calls


def test_discovery_falls_back_to_site_root_paths() -> None:
    """A /blog page whose feed lives at the site root, not under /blog."""

    def fetch(url: str) -> bytes:
        if url == "https://www.example.com/blog":
            return b"<html><body>no feed tags here</body></html>"
        if url == "https://www.example.com/feed":
            return RSS_FEED
        raise FetchError("404")

    discovery = discover_feed("https://www.example.com/blog", fetcher=fetch)
    assert discovery.feed_url == "https://www.example.com/feed"
    # given-path probes were tried before root probes
    assert discovery.probed.index("https://www.example.com/blog/feed") < discovery.probed.index(
        "https://www.example.com/feed"
    )


def test_discovery_probe_count_stays_bounded() -> None:
    anchors = "".join(f'<a href="/x{i}/rss.xml">r</a>' for i in range(20))

    def fetch(url: str) -> bytes:
        if url == "https://www.example.com/blog":
            return f"<html><body>{anchors}</body></html>".encode()
        raise FetchError("404")

    discovery = discover_feed("https://www.example.com/blog", fetcher=fetch)
    assert discovery.feed_url is None
    # 1 page GET + at most 16 candidate GETs, despite 20 anchors and 12 paths
    assert len(discovery.probed) <= 17
