"""Outreach briefs: model proposes, deterministic validation disposes, nothing is sent."""

import json
from pathlib import Path

import pytest

from wingman.application.corpus import add_to_corpus
from wingman.application.ingest import IngestError
from wingman.application.outreach import build_outreach_brief, render_outreach_brief
from wingman.application.people import add_person, fetch_person_feed
from wingman.application.pov import build_pov_card
from wingman.application.similarity import embed_missing
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelRequest, ModelResponse
from wingman.providers.embeddings import HashedEmbeddingProvider

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
    """Returns a canned JSON proposal; records the prompt it was given."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.last_prompt: str | None = None

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.last_prompt = request.prompt
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


def seeded_workspace(workspace: Path, storage: Storage, config) -> str:
    """Jane with a POV card + one corpus essay; returns the corpus doc_id."""
    person, _ = add_person("Jane Author", storage, substack_url="https://jane.substack.com")
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
                "topics": ["explainable analytics"],
            }
        ),
    )
    essay = workspace / "essay.md"
    essay.write_text(
        "Explainable analytics pipelines beat black boxes in production.", encoding="utf-8"
    )
    add_to_corpus(essay, "writing", config, storage)
    return storage.list_corpus_documents()[0].doc_id


def test_valid_points_are_stored_with_alignment(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        corpus_id = seeded_workspace(workspace, storage, config)
        embed_missing(storage, HashedEmbeddingProvider())
        provider = ScriptedProvider(
            {
                "talking_points": [
                    {
                        "point": "You both argue analytics must show its reasoning.",
                        "their_stance": STANCE,
                        "corpus_doc_id": corpus_id,
                        "your_quote": "Explainable analytics pipelines beat black boxes",
                    }
                ],
                "intro": "Hi Jane — your case for explainable answers matches what I build.",
            }
        )
        report = build_outreach_brief("Jane Author", storage, provider)
        assert provider.last_prompt is not None
        assert corpus_id in provider.last_prompt and STANCE in provider.last_prompt
        assert len(report.brief.talking_points) == 1
        assert report.brief.talking_points[0].corpus_doc_title.startswith("Explainable analytics")
        assert report.brief.alignment is not None
        assert report.brief.draft_intro.startswith("Hi Jane")

        person = storage.find_person_by_name_key("jane author")
        assert person is not None
        stored = storage.get_outreach_brief(person.person_id)
        assert stored is not None
        rendered = render_outreach_brief(stored)
        assert "nothing is sent" in rendered and "RFC-006" in rendered
        assert "black boxes" in rendered and "Alignment" in rendered


def test_fabricated_quotes_stances_and_docs_are_rejected(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        corpus_id = seeded_workspace(workspace, storage, config)
        provider = ScriptedProvider(
            {
                "talking_points": [
                    {
                        "point": "Real point.",
                        "their_stance": STANCE,
                        "corpus_doc_id": corpus_id,
                        "your_quote": "black boxes in production",
                    },
                    {
                        "point": "Fabricated quote.",
                        "their_stance": STANCE,
                        "corpus_doc_id": corpus_id,
                        "your_quote": "I never wrote this sentence",
                    },
                    {
                        "point": "Fabricated stance.",
                        "their_stance": "Believes dashboards are dead.",
                        "corpus_doc_id": corpus_id,
                        "your_quote": "black boxes in production",
                    },
                    {
                        "point": "Phantom document.",
                        "their_stance": STANCE,
                        "corpus_doc_id": "not-a-real-doc",
                        "your_quote": "anything",
                    },
                ],
                "intro": "",
            }
        )
        report = build_outreach_brief("Jane Author", storage, provider)
        assert len(report.brief.talking_points) == 1
        assert len(report.rejected) == 3
        reasons = " ".join(item.reason for item in report.rejected)
        assert "verbatim" in reasons
        assert "does not match any stance" in reasons
        assert "not among the supplied documents" in reasons
        # no embeddings in this test: alignment degrades to None, brief still stored
        assert report.brief.alignment is None


def test_point_limit_and_no_survivors(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        corpus_id = seeded_workspace(workspace, storage, config)
        over_limit = ScriptedProvider(
            {
                "talking_points": [
                    {
                        "point": f"Point number {index}.",
                        "their_stance": STANCE,
                        "corpus_doc_id": corpus_id,
                        "your_quote": "black boxes in production",
                    }
                    for index in range(8)
                ],
                "intro": "",
            }
        )
        report = build_outreach_brief("Jane Author", storage, over_limit)
        assert len(report.brief.talking_points) == 5
        assert len([item for item in report.rejected if "limit" in item.reason]) == 3

        all_bad = ScriptedProvider(
            {
                "talking_points": [
                    {
                        "point": "Bad.",
                        "their_stance": STANCE,
                        "corpus_doc_id": corpus_id,
                        "your_quote": "never wrote this",
                    }
                ],
                "intro": "",
            }
        )
        with pytest.raises(IngestError, match="no talking point survived"):
            build_outreach_brief("Jane Author", storage, all_bad)
        # the earlier valid brief is still the stored one
        person = storage.find_person_by_name_key("jane author")
        assert person is not None
        stored = storage.get_outreach_brief(person.person_id)
        assert stored is not None and len(stored.talking_points) == 5


def test_brief_requires_person_card_and_corpus(workspace: Path) -> None:
    config = load_config()
    provider = ScriptedProvider({"talking_points": [], "intro": ""})
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="no person named"):
            build_outreach_brief("Nobody", storage, provider)
        add_person("No Card", storage)
        with pytest.raises(IngestError, match="no POV card"):
            build_outreach_brief("No Card", storage, provider)
        # card exists but the corpus is empty
        person, _ = add_person("Jane Author", storage, substack_url="https://jane.substack.com")
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
                            "quote": "show its work",
                            "doc_id": doc_id,
                        }
                    ],
                    "topics": [],
                }
            ),
        )
        with pytest.raises(IngestError, match="corpus is empty"):
            build_outreach_brief("Jane Author", storage, provider)
