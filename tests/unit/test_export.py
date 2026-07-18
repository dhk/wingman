"""Print-ready exports: Letter format, design-system classes, clickable links."""

import json
from pathlib import Path

import pytest

from wingman.application.corpus import add_to_corpus
from wingman.application.ingest import IngestError
from wingman.application.outreach import build_outreach_brief
from wingman.application.people import add_person, fetch_person_feed
from wingman.application.pov import build_pov_card
from wingman.domain.profile import EvidenceSpan, ProfileItem, ProfileItemKind
from wingman.domain.provenance import ClaimClassification
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelRequest, ModelResponse
from wingman.reporting.export import export_career, export_company, export_person

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><item>
    <title>Explainable Analytics</title>
    <link>https://jane.substack.com/p/explainable</link>
    <content:encoded><![CDATA[<p>Every answer should show its work the way an analyst would.</p>]]></content:encoded>
  </item></channel></rss>
"""

STANCE = "Believes analytics answers must be explainable."


class ScriptedProvider:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            text=json.dumps(self._payload), provider="scripted", model="scripted-1", latency_ms=0
        )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return tmp_path


def test_career_export_is_letter_portrait_with_cited_cards(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        storage.add_profile_item(
            ProfileItem(
                kind=ProfileItemKind.ACHIEVEMENT,
                name="Search <rewrite>",
                detail="Shipped it",
                classification=ClaimClassification.FACT,
                confidence=0.9,
                evidence=[EvidenceSpan(source_record_id="r1", quote="Shipped the search rewrite")],
                prompt_version="v1",
                extracted_by="test",
            )
        )
        storage.add_profile_item(
            ProfileItem(
                kind=ProfileItemKind.SKILL,
                name="Python",
                detail="",
                classification=ClaimClassification.FACT,
                confidence=0.95,
                evidence=[EvidenceSpan(source_record_id="r1", quote="Skills: Python")],
                prompt_version="v1",
                extracted_by="test",
            )
        )
        path = export_career(config, storage)
    text = path.read_text(encoding="utf-8")
    assert "format: Letter" in text and "landscape" not in text
    # absolute stylesheet path: md-to-pdf resolves it against the process cwd,
    # so a relative name only worked when rendering from inside reports/pdf/
    assert f'stylesheet: "{path.parent / "wingman-pdf.css"}"' in text
    assert (path.parent / "wingman-pdf.css").exists()
    # cards carry the claim, the tag, and the verbatim quote — HTML-escaped
    assert "Search &lt;rewrite&gt;" in text
    assert 'class="tag tag-fact"' in text and "fact · 0.90" in text
    assert "<blockquote>Shipped the search rewrite</blockquote>" in text
    assert 'class="tag tag-skill"' in text and "Python" in text


def test_career_export_fails_visibly_on_empty_profile(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="profile is empty"):
            export_career(config, storage)


def test_person_export_is_landscape_three_columns_with_links(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person(
            "Jane Author",
            storage,
            substack_url="https://jane.substack.com",
            company="ExplainCo",
            position="Head of Data",
        )
        fetch_person_feed(person, config, storage, fetcher=lambda url: FEED.encode())
        doc_id = storage.list_external_documents(person.person_id)[0].doc_id
        build_pov_card(
            "Jane Author",
            storage,
            ScriptedProvider(
                {
                    "stances": [
                        {
                            "statement": STANCE,
                            "quote": "Every answer should show its work",
                            "doc_id": doc_id,
                        }
                    ],
                    "topics": ["explainability"],
                }
            ),
        )
        essay = workspace / "essay.md"
        essay.write_text("Explainable pipelines beat black boxes.", encoding="utf-8")
        add_to_corpus(essay, "writing", config, storage)
        corpus_id = storage.list_corpus_documents()[0].doc_id
        build_outreach_brief(
            "Jane Author",
            storage,
            ScriptedProvider(
                {
                    "talking_points": [
                        {
                            "point": "You both want reasoning shown.",
                            "their_stance": STANCE,
                            "corpus_doc_id": corpus_id,
                            "your_quote": "Explainable pipelines beat black boxes.",
                        }
                    ],
                    "intro": "Hi Jane — brief draft.",
                }
            ),
        )
        path = export_person("jane", config, storage)

    text = path.read_text(encoding="utf-8")
    assert "landscape: true" in text and "format: Letter" in text
    assert '<div class="sheet">' in text
    for column in ("Outreach Brief", "Point of View", "Related"):
        assert f"<h2>{column}</h2>" in text
    # clickable links: the stance's source post and the Substack itself
    assert 'href="https://jane.substack.com/p/explainable"' in text
    assert 'href="https://jane.substack.com"' in text
    # two-voice talking point and the never-sends panel
    assert "they argue" in text and "you wrote" in text
    assert "wingman never sends (RFC-006)" in text
    assert "Head of Data · ExplainCo" in text


def test_person_export_requires_some_data(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Empty Person", storage)
        with pytest.raises(IngestError, match="nothing to export"):
            export_person("Empty Person", config, storage)
        with pytest.raises(IngestError, match="no person named"):
            export_person("Nobody", config, storage)


def test_company_export_upgrades_labels_and_links(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person(
            "Ana", storage, substack_url="https://ana.substack.com", company="DataCo"
        )
        fetch_person_feed(person, config, storage, fetcher=lambda url: FEED.encode())
        doc_id = storage.list_external_documents(person.person_id)[0].doc_id
        build_pov_card(
            "Ana",
            storage,
            ScriptedProvider(
                {
                    "stances": [
                        {
                            "statement": STANCE,
                            "quote": "show its work",
                            "doc_id": doc_id,
                        }
                    ],
                    "topics": [],
                }
            ),
        )
        path = export_company("DataCo", config, storage)
    text = path.read_text(encoding="utf-8")
    assert "format: Letter" in text and "landscape" not in text
    assert '<span class="tag tag-inference">inference</span>' in text
    assert '<span class="tag tag-fact">fact</span>' in text
    assert "[inference]" not in text and "[fact]" not in text
