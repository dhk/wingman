"""'people discover' walks watched Substacks' public recommendations, suggestion-only."""

from pathlib import Path

import pytest

import wingman.application.people as people_module
from wingman.application.people import (
    add_person,
    discover_recommendations,
)
from wingman.infrastructure.config import load_config
from wingman.infrastructure.fetch import FetchError
from wingman.infrastructure.storage import Storage

PAGE_TEMPLATE = """<html><body>
<a href="https://substack.com/home">substack chrome</a>
<a href="https://www.substack.com/about">more chrome</a>
<a href="https://open.substack.com/pub/whatever">reader link</a>
{links}
</body></html>
"""

# add_person now verifies a passed substack_url's feed before storing it
# (#483) -- a minimal valid feed so that verification passes; these tests
# are about discover_recommendations, not feed content.
VALID_FEED = b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'


def page(*publications: str) -> bytes:
    links = "\n".join(f'<a href="https://{pub}.substack.com/">{pub}</a>' for pub in publications)
    return PAGE_TEMPLATE.format(links=links).encode("utf-8")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    monkeypatch.setattr(people_module, "fetch_url", lambda url: VALID_FEED)
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return tmp_path


def test_discover_ranks_by_recommender_count(workspace: Path) -> None:
    config = load_config()
    pages = {
        "https://alpha.substack.com/recommendations": page("shared", "unique-a"),
        "https://beta.substack.com/recommendations": page("shared", "unique-b"),
    }
    with Storage(config.db_path) as storage:
        add_person("Alpha", storage, substack_url="https://alpha.substack.com")
        add_person("Beta", storage, substack_url="https://beta.substack.com")
        report = discover_recommendations(storage, fetcher=lambda url: pages[url])
        assert report.scanned == 2
        assert report.failures == []
        assert [c.url for c in report.candidates] == [
            "https://shared.substack.com",
            "https://unique-a.substack.com",
            "https://unique-b.substack.com",
        ]
        assert sorted(report.candidates[0].recommenders) == ["Alpha", "Beta"]


def test_discover_excludes_watched_self_and_chrome(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Alpha", storage, substack_url="https://alpha.substack.com")
        add_person("Beta", storage, substack_url="https://beta.substack.com")
        # alpha recommends beta (already watched), itself, and one new pub
        recommendations = page("beta", "alpha", "fresh")

        def fetch(url: str) -> bytes:
            if url.startswith("https://alpha"):
                return recommendations
            return page()

        report = discover_recommendations(storage, fetcher=fetch)
        assert [c.url for c in report.candidates] == ["https://fresh.substack.com"]


def test_discover_tolerates_per_publication_failures(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Alpha", storage, substack_url="https://alpha.substack.com")
        add_person("Beta", storage, substack_url="https://beta.substack.com")

        def fetch(url: str) -> bytes:
            if "alpha" in url:
                raise FetchError("connection refused")
            return page("fresh")

        report = discover_recommendations(storage, fetcher=fetch)
        assert report.scanned == 1
        assert len(report.failures) == 1 and "Alpha" in report.failures[0]
        assert [c.url for c in report.candidates] == ["https://fresh.substack.com"]


def test_discover_with_no_watched_substacks(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("No Feed", storage)
        report = discover_recommendations(storage, fetcher=lambda url: page())
        assert report.scanned == 0
        assert report.candidates == []


def test_discover_strips_userinfo_and_ports_from_links(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Alpha", storage, substack_url="https://alpha.substack.com")
        crafted = (
            b'<html><body><a href="https://user:pw@tricky.substack.com:8443/">x</a>'
            b'<a href="https://evil@beta.substack.com/">y</a></body></html>'
        )
        add_person("Beta", storage, substack_url="https://beta.substack.com")

        def fetch(url: str) -> bytes:
            return crafted if "alpha" in url else page()

        report = discover_recommendations(storage, fetcher=fetch)
        # hostnames only: no credentials or ports in candidates, and the
        # credential-bearing link to a watched publication is still excluded
        assert [c.url for c in report.candidates] == ["https://tricky.substack.com"]


def test_discover_respects_limit(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Alpha", storage, substack_url="https://alpha.substack.com")
        many = page(*[f"pub{i}" for i in range(15)])
        report = discover_recommendations(storage, fetcher=lambda url: many, limit=5)
        assert len(report.candidates) == 5
