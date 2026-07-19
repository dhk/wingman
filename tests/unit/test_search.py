"""Unified search (RFC-020): one query, every store, honest attribution."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from wingman.application.corpus import add_to_corpus
from wingman.application.ingest import IngestError
from wingman.application.people import add_person, fetch_person_feed
from wingman.application.research import add_company_source, research_company
from wingman.application.search import render_search_report, search_workspace
from wingman.domain.outreach import OutreachBrief, OutreachPurpose, TalkingPoint
from wingman.domain.person import NewsItem
from wingman.domain.pov import PovCard, Stance, StanceDimension
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><item>
    <title>Semantic Layers</title>
    <link>https://jane.substack.com/p/semantic-layers</link>
    <content:encoded><![CDATA[<p>The semantic layer is where meaning lives.</p>]]></content:encoded>
  </item></channel></rss>
"""

CAREERS = b'<html><body><a href="/jobs/semantic-engineer">Semantic Engineer</a></body></html>'


@pytest.fixture
def loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Storage:
    """A workspace with one hit for 'semantic' in every store."""
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    essay = tmp_path / "essay.md"
    essay.write_text("# My Take\n\nSemantic clarity beats clever code.\n", encoding="utf-8")
    with Storage(config.db_path) as storage:
        add_to_corpus(essay, "writing", config, storage)
        person, _ = add_person(
            "Jane Author", storage, substack_url="https://jane.substack.com", company="Acme"
        )
        fetch_person_feed(person, config, storage, fetcher=lambda url: FEED.encode())
        doc = storage.list_external_documents(person.person_id)[0]
        storage.save_pov_card(
            PovCard(
                person_id=person.person_id,
                person_name="Jane Author",
                stances=[
                    Stance(
                        statement="Believes semantic layers carry the meaning.",
                        quote="The semantic layer is where meaning lives.",
                        doc_id=doc.doc_id,
                        doc_title=doc.title,
                        source_record_id=doc.source_record_id,
                        dimension=StanceDimension.TECHNICAL,
                    )
                ],
                topics=["semantic layers"],
                documents_used=1,
                provider="scripted",
                model="scripted-1",
                prompt_version="v2",
            )
        )
        storage.replace_person_news(
            person.person_id,
            [
                NewsItem(
                    person_id=person.person_id,
                    title="Acme ships a semantic engine",
                    url="https://news.example.com/acme-semantic",
                    published_at=datetime(2026, 7, 1, tzinfo=UTC),
                )
            ],
        )
        add_company_source("Acme", "https://acme.example.com/careers", storage)
        research_company("Acme", storage, fetcher=lambda url: CAREERS)
        storage.save_outreach_brief(
            OutreachBrief(
                person_id=person.person_id,
                person_name="Jane Author",
                talking_points=[
                    TalkingPoint(
                        point="You both treat semantic modeling as the real work.",
                        their_stance="Believes semantic layers carry the meaning.",
                        your_quote="Semantic clarity beats clever code.",
                        corpus_doc_id="d1",
                        corpus_doc_title="My Take",
                        dimension=StanceDimension.TECHNICAL,
                    )
                ],
                intro_points=["Open with the semantic-authority question."],
                purpose=OutreachPurpose.ADVICE,
                corpus_documents_used=1,
                pov_generated_at=datetime(2026, 7, 10, tzinfo=UTC),
                provider="scripted",
                model="scripted-1",
                prompt_version="v2",
            )
        )
        yield storage


def test_search_sweeps_every_store(loaded: Storage) -> None:
    report = search_workspace("semantic", loaded, limit=20)
    kinds = {hit.kind for hit in report.hits}
    assert kinds == {"corpus", "writing", "stance", "news", "research-link", "brief"}
    assert report.searched == ["corpus", "writing", "stances", "news", "research", "briefs"]
    by_kind = {hit.kind: hit for hit in report.hits}
    assert by_kind["corpus"].who == "you"
    assert by_kind["writing"].who == "Jane Author"
    assert by_kind["writing"].source == "https://jane.substack.com/p/semantic-layers"
    assert by_kind["stance"].title.startswith("[technical]")
    assert by_kind["news"].title == "Acme ships a semantic engine"
    assert by_kind["research-link"].title == "https://acme.example.com/jobs/semantic-engineer"
    assert by_kind["brief"].who == "Jane Author"
    # every store's rank-1 precedes any rank-2 (interleaving)
    first_six = report.hits[:6]
    assert all(hit.rank == 1 for hit in first_six)


def test_search_is_selective_and_honest_when_empty(loaded: Storage) -> None:
    report = search_workspace("blockchain", loaded)
    assert report.hits == []
    rendered = render_search_report(report)
    assert "Nothing in the workspace matches" in rendered
    assert "corpus" in rendered  # says what it searched

    narrower = search_workspace("jobs", loaded, limit=20)
    assert {hit.kind for hit in narrower.hits} == {"research-link"}


def test_search_and_semantics_and_errors(loaded: Storage) -> None:
    # AND semantics: both tokens must appear
    both = search_workspace("semantic meaning", loaded, limit=20)
    assert all("meaning" in (hit.title + hit.snippet).lower() for hit in both.hits)
    with pytest.raises(IngestError, match="empty"):
        search_workspace("   ", loaded)
    with pytest.raises(IngestError, match="search failed"):
        search_workspace('"unbalanced', loaded)


def test_render_lists_attribution_and_sources(loaded: Storage) -> None:
    rendered = render_search_report(search_workspace("semantic", loaded, limit=20))
    assert "[stance]" in rendered and "[news]" in rendered
    assert "Jane Author" in rendered
    assert "https://news.example.com/acme-semantic" in rendered
