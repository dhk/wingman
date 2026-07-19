"""Follow + overnight (RFC-018): enrollment is consent; the digest is honest."""

from pathlib import Path

import pytest

from wingman.application.focus import (
    OVERNIGHT_LIST,
    follow_company,
    overnight_run,
    render_follow_report,
)
from wingman.application.ingest import IngestError
from wingman.application.people import add_person
from wingman.application.research import list_company_sources
from wingman.infrastructure.config import load_config
from wingman.infrastructure.fetch import FetchError
from wingman.infrastructure.storage import Storage

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><item>
    <title>On Evals</title>
    <link>https://jane.substack.com/p/on-evals</link>
    <content:encoded><![CDATA[<p>Evaluations before autonomy, always.</p>]]></content:encoded>
  </item></channel></rss>
"""

CAREERS_PAGE = b'<html><body><a href="/jobs/researcher">Researcher</a></body></html>'


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    config.models_config_path.write_text(
        '[models.embed_semantic]\nprovider = "hashed"\n', encoding="utf-8"
    )
    return tmp_path


def test_follow_enrolls_company_and_sourced_people(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage, substack_url="https://jane.substack.com", company="Acme")
        add_person("Quiet Contact", storage, company="Acme")
        add_person("Other Person", storage, company="Elsewhere")

        def probe(url: str) -> bytes:
            if url.endswith("/careers") or url.endswith("/blog"):
                return CAREERS_PAGE
            raise FetchError("404")

        report = follow_company("acme", storage, url="https://acme.example.com", fetcher=probe)
        assert report.company == "Acme" and report.company_enrolled
        assert report.people_enrolled == ["Jane Author"]  # has writing attached
        assert report.known_without_sources == ["Quiet Contact"]  # suggested, not enrolled
        assert report.sources_approved == [
            "https://acme.example.com/careers",
            "https://acme.example.com/blog",
        ]
        assert {u.url for u in list_company_sources("Acme", storage)} == set(
            report.sources_approved
        )
        members = storage.watchlist_members(OVERNIGHT_LIST)
        assert ("company", "Acme") in members and ("person", "Jane Author") in members
        assert ("person", "Quiet Contact") not in members
        assert ("person", "Other Person") not in members

        # idempotent: following again enrolls and approves nothing new
        again = follow_company("Acme", storage, url="https://acme.example.com", fetcher=probe)
        assert not again.company_enrolled
        assert again.people_enrolled == [] and again.sources_approved == []
        rendered = render_follow_report(report)
        assert "Jane Author" in rendered and "wingman overnight" in rendered


def test_follow_rejects_bad_input(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="empty"):
            follow_company("  ", storage)
        with pytest.raises(IngestError, match="https"):
            follow_company("Acme", storage, url="http://acme.example.com")


def test_overnight_requires_enrollment(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="follow"):
            overnight_run(config, storage)


def test_overnight_runs_targets_and_writes_digest(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.application.news as news_module
    import wingman.application.people as people_module
    import wingman.application.research as research_module

    config = load_config()
    monkeypatch.setattr(people_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(news_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(research_module, "fetch_url", lambda url: CAREERS_PAGE)
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage, substack_url="https://jane.substack.com", company="Acme")
        follow_company(
            "Acme", storage, url="https://acme.example.com", fetcher=lambda url: CAREERS_PAGE
        )
        report = overnight_run(config, storage)

    assert report.processed == 2  # the company and Jane
    kinds = {(target.name, target.kind) for target in report.targets}
    assert kinds == {("Acme", "company"), ("Jane Author", "person")}
    digest = Path(report.digest_path)
    assert digest.exists() and "digests" in str(digest)
    text = digest.read_text(encoding="utf-8")
    assert "# Overnight digest" in text
    assert "Acme (company)" in text and "Jane Author (person)" in text
    assert "research https://acme.example.com/careers" in text
    # no synthesize model in tests: model steps skipped honestly, run not fatal
    assert "themes skipped" in text
    assert "fetch: ok" in text
    # the action list closes the digest: fresh writing -> a read/outreach action
    assert "## Action list" in text
    assert text.index("## Action list") > text.index("Jane Author (person)")
    assert "Read Jane Author's new writing" in text
    assert "why: 1 new post(s) fetched overnight" in text
    assert "evidence: On Evals — https://jane.substack.com/p/on-evals" in text
    # latest.md mirrors the newest digest
    latest = digest.parent / "latest.md"
    assert latest.exists() and latest.read_text(encoding="utf-8") == text


def test_overnight_reports_failures_without_dying(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.application.news as news_module
    import wingman.application.people as people_module
    import wingman.application.research as research_module

    def boom(url: str) -> bytes:
        raise FetchError("network down")

    config = load_config()
    monkeypatch.setattr(people_module, "fetch_url", boom)
    monkeypatch.setattr(news_module, "fetch_url", boom)
    monkeypatch.setattr(research_module, "fetch_url", boom)
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage, substack_url="https://jane.substack.com", company="Acme")
        follow_company("Acme", storage)  # no url: no sources approved
        report = overnight_run(config, storage)
    text = Path(report.digest_path).read_text(encoding="utf-8")
    assert "research skipped" in text  # no approved sources, said so
    assert "fetch: failed" in text  # feed failure reported per-step
    assert report.processed == 2


def test_second_run_actions_carry_new_link_evidence(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.application.news as news_module
    import wingman.application.people as people_module
    import wingman.application.research as research_module

    config = load_config()
    monkeypatch.setattr(people_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(news_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(research_module, "fetch_url", lambda url: CAREERS_PAGE)
    grown = (
        b'<html><body><a href="/jobs/researcher">R</a><a href="/jobs/staff-mle">S</a></body></html>'
    )
    with Storage(config.db_path) as storage:
        follow_company(
            "Acme",
            storage,
            url="https://acme.example.com",
            fetcher=lambda url: (
                CAREERS_PAGE
                if url.endswith("/careers")
                else (_ for _ in ()).throw(FetchError("404"))
            ),
        )
        overnight_run(config, storage)  # baseline snapshot
        monkeypatch.setattr(research_module, "fetch_url", lambda url: grown)
        report = overnight_run(config, storage)
    action = next(a for a in report.actions if "opening" in a.what)
    assert action.who == "Acme"
    assert "1 new link since" in action.why
    assert action.evidence == [
        "https://acme.example.com/jobs/staff-mle",
        'run: wingman assess --url "https://acme.example.com/jobs/staff-mle"',
    ]
    text = Path(report.digest_path).read_text(encoding="utf-8")
    assert "**Assess the new opening(s) at Acme**" in text


def test_latest_digest_and_out_dir(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from wingman.application.focus import latest_digest

    import wingman.application.news as news_module
    import wingman.application.people as people_module

    config = load_config()
    assert latest_digest(config) is None
    monkeypatch.setattr(people_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(news_module, "fetch_url", lambda url: FEED)
    custom = Path(str(workspace)) / "desk"
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage, substack_url="https://jane.substack.com")
        storage.watchlist_add(OVERNIGHT_LIST, "person", "Jane Author")
        report = overnight_run(config, storage, out_dir=custom)
    assert Path(report.digest_path).parent == custom.resolve()
    assert (custom / "latest.md").exists()
    # default-location helper ignores custom out_dirs (they are the user's copy)
    assert latest_digest(config) is None
