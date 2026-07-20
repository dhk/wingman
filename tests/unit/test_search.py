"""Unified search (RFC-022): one query, every store, honest attribution."""

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
    report = search_workspace("semantic", loaded, load_config(), limit=20)
    kinds = {hit.kind for hit in report.hits}
    assert kinds == {"corpus", "writing", "stance", "news", "research-link", "brief"}
    assert report.searched == [
        "corpus",
        "writing",
        "semantic",
        "stances",
        "news",
        "research",
        "digests",
        "briefs",
        "answers",
    ]
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
    report = search_workspace("blockchain", loaded, load_config())
    assert report.hits == []
    rendered = render_search_report(report)
    assert "Nothing in the workspace matches" in rendered
    assert "corpus" in rendered  # says what it searched

    narrower = search_workspace("jobs", loaded, load_config(), limit=20)
    assert {hit.kind for hit in narrower.hits} == {"research-link"}


def test_search_and_semantics_and_errors(loaded: Storage) -> None:
    # AND semantics: both tokens must appear
    both = search_workspace("semantic meaning", loaded, load_config(), limit=20)
    assert all("meaning" in (hit.title + hit.snippet).lower() for hit in both.hits)
    with pytest.raises(IngestError, match="empty"):
        search_workspace("   ", loaded, load_config())
    # FTS5 syntax in the query is neutralized, never fatal (#68)
    assert search_workspace('"unbalanced', loaded, load_config()).searched


@pytest.fixture
def embedded(loaded: Storage) -> Storage:
    """The loaded workspace with keyless local (hashed) embeddings built."""
    from wingman.application.similarity import embed_missing
    from wingman.providers.router import get_embedding_provider

    config = load_config()
    config.models_config_path.write_text(
        '[models.embed_semantic]\nprovider = "hashed"\n', encoding="utf-8"
    )
    embed_missing(loaded, get_embedding_provider(config))
    return loaded


def test_semantic_pass_finds_meaning_matches(embedded: Storage) -> None:
    # 'ontology' appears in no document, so keyword AND matching finds
    # nothing anywhere — the semantic pass still surfaces both documents
    # through their shared-meaning overlap with 'semantic'.
    report = search_workspace("semantic ontology", embedded, load_config(), limit=20)
    assert {hit.kind for hit in report.hits} == {"semantic"}
    assert {hit.title for hit in report.hits} == {"My Take", "Semantic Layers"}
    assert all("similarity 0." in hit.snippet for hit in report.hits)
    assert report.notes == []  # the pass ran; nothing to explain
    # a genuinely unrelated query stays empty — the threshold holds
    assert search_workspace("blockchain", embedded, load_config()).hits == []


def test_semantic_pass_dedupes_keyword_hits(embedded: Storage) -> None:
    # both documents already match 'semantic' by keyword, so the semantic
    # column adds nothing — no duplicate rows for the same document
    report = search_workspace("semantic", embedded, load_config(), limit=20)
    assert not any(hit.kind == "semantic" for hit in report.hits)
    titles = [hit.title for hit in report.hits if hit.kind in {"corpus", "writing"}]
    assert len(titles) == len(set(titles))


def test_semantic_pass_skips_visibly_without_embeddings(loaded: Storage) -> None:
    report = search_workspace("semantic", loaded, load_config(), limit=20)
    assert any("semantic pass skipped" in note for note in report.notes)
    assert "semantic pass skipped" in render_search_report(report)


def test_render_lists_attribution_and_sources(loaded: Storage) -> None:
    rendered = render_search_report(search_workspace("semantic", loaded, load_config(), limit=20))
    assert "[stance]" in rendered and "[news]" in rendered
    assert "Jane Author" in rendered
    assert "https://news.example.com/acme-semantic" in rendered
