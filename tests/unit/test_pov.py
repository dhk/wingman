"""POV cards: model proposes, deterministic validation disposes."""

import json
from pathlib import Path

import pytest

from wingman.agents.profile_curator import ProposalParseError
from wingman.application.ingest import IngestError
from wingman.application.people import add_person, fetch_person_feed
from wingman.application.pov import build_pov_card, render_pov_card
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelRequest, ModelResponse

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><item>
    <title>Explainable Analytics</title>
    <link>https://jane.substack.com/p/explainable</link>
    <content:encoded><![CDATA[<p>Every answer should show its work the way an analyst would.</p>]]></content:encoded>
  </item></channel></rss>
"""


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


def seeded_storage(storage: Storage, config) -> str:
    """Add Jane with one fetched post; return her doc_id."""
    person, _ = add_person("Jane Author", storage, substack_url="https://jane.substack.com")
    fetch_person_feed(person, config, storage, fetcher=lambda url: FEED.encode())
    return storage.list_external_documents(person.person_id)[0].doc_id


def test_valid_stances_are_stored_with_provenance(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        doc_id = seeded_storage(storage, config)
        provider = ScriptedProvider(
            {
                "stances": [
                    {
                        "statement": "Believes analytics answers must be explainable.",
                        "quote": "Every answer should show its work",
                        "doc_id": doc_id,
                        "dimension": "technical",
                    },
                    {
                        "statement": "Values analysts over dashboards.",
                        "quote": "the way an analyst would",
                        "doc_id": doc_id,
                        "dimension": "vibes",  # not in the taxonomy
                    },
                ],
                "topics": ["explainable AI", "analytics"],
            }
        )
        report = build_pov_card("Jane Author", storage, provider)
        assert provider.last_prompt is not None and doc_id in provider.last_prompt
        assert len(report.card.stances) == 2
        stance = report.card.stances[0]
        assert stance.doc_title == "Explainable Analytics"
        assert storage.get_source_record(stance.source_record_id) is not None
        assert report.card.topics == ["explainable AI", "analytics"]
        # a valid dimension is kept; an invalid one is stored uncategorized, never guessed
        from wingman.domain.pov import StanceDimension

        assert stance.dimension is StanceDimension.TECHNICAL
        assert report.card.stances[1].dimension is None

        # the card is stored and retrievable
        person = storage.find_person_by_name_key("jane author")
        assert person is not None
        stored = storage.get_pov_card(person.person_id)
        assert stored is not None and stored.stances[0].statement == stance.statement
        rendered = render_pov_card(stored)
        assert "Jane Author" in rendered and "show its work" in rendered
        assert "- [technical] Believes analytics" in rendered
        assert "[vibes]" not in rendered  # uncategorized renders without a label


def test_fabricated_quotes_and_unknown_docs_are_rejected(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        doc_id = seeded_storage(storage, config)
        provider = ScriptedProvider(
            {
                "stances": [
                    {
                        "statement": "Real stance.",
                        "quote": "Every answer should show its work",
                        "doc_id": doc_id,
                    },
                    {
                        "statement": "Fabricated stance.",
                        "quote": "I secretly hate dashboards",
                        "doc_id": doc_id,
                    },
                    {
                        "statement": "Phantom-document stance.",
                        "quote": "anything",
                        "doc_id": "not-a-real-doc",
                    },
                ],
                "topics": [],
            }
        )
        report = build_pov_card("Jane Author", storage, provider)
        assert len(report.card.stances) == 1
        assert len(report.rejected) == 2
        reasons = " ".join(item.reason for item in report.rejected)
        assert "verbatim" in reasons and "not among the supplied documents" in reasons


def test_stance_limit_is_enforced_deterministically(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        doc_id = seeded_storage(storage, config)
        provider = ScriptedProvider(
            {
                "stances": [
                    {
                        "statement": f"Stance number {index}.",
                        "quote": "Every answer should show its work",
                        "doc_id": doc_id,
                    }
                    for index in range(9)
                ],
                "topics": [],
            }
        )
        report = build_pov_card("Jane Author", storage, provider)
        assert len(report.card.stances) == 6
        over_limit = [item for item in report.rejected if "limit" in item.reason]
        assert len(over_limit) == 3


def test_no_surviving_stance_stores_nothing(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        doc_id = seeded_storage(storage, config)
        provider = ScriptedProvider(
            {
                "stances": [
                    {"statement": "Fabricated.", "quote": "never said this", "doc_id": doc_id}
                ],
                "topics": [],
            }
        )
        with pytest.raises(IngestError, match="no stance survived"):
            build_pov_card("Jane Author", storage, provider)
        person = storage.find_person_by_name_key("jane author")
        assert person is not None
        assert storage.get_pov_card(person.person_id) is None


def test_pov_requires_person_and_writing(workspace: Path) -> None:
    config = load_config()
    provider = ScriptedProvider({"stances": [], "topics": []})
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="no person named"):
            build_pov_card("Nobody", storage, provider)
        add_person("No Writing", storage)
        with pytest.raises(IngestError, match="no stored writing"):
            build_pov_card("No Writing", storage, provider)


def test_unparseable_model_output_raises(workspace: Path) -> None:
    config = load_config()

    class GarbageProvider:
        def complete(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse(text="not json", provider="x", model="x", latency_ms=0)

    with Storage(config.db_path) as storage:
        seeded_storage(storage, config)
        with pytest.raises(ProposalParseError):
            build_pov_card("Jane Author", storage, GarbageProvider())


def test_own_pov_builds_from_the_corpus(workspace: Path) -> None:
    from wingman.application.corpus import add_to_corpus
    from wingman.application.pov import CORPUS_PERSON_ID, build_own_pov
    from wingman.infrastructure.config import load_config

    config = load_config()
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="corpus is empty"):
            build_own_pov(storage, ScriptedProvider({"stances": [], "topics": []}))
        essay = workspace / "essay.md"
        essay.write_text(
            "Answers must show their work. Explainability beats dashboards.", encoding="utf-8"
        )
        add_to_corpus(essay, "writing", config, storage)
        doc_id = storage.list_corpus_documents()[0].doc_id
        provider = ScriptedProvider(
            {
                "stances": [
                    {
                        "statement": "The author believes answers must show their work.",
                        "quote": "Answers must show their work",
                        "doc_id": doc_id,
                        "dimension": "values",
                    },
                    {
                        "statement": "Fabricated.",
                        "quote": "never wrote this",
                        "doc_id": doc_id,
                    },
                ],
                "topics": ["explainability"],
            }
        )
        report = build_own_pov(storage, provider)
        assert provider.last_prompt is not None and "the author" in provider.last_prompt
        assert len(report.card.stances) == 1 and len(report.rejected) == 1
        stored = storage.get_pov_card(CORPUS_PERSON_ID)
        assert stored is not None and stored.person_name == "Your corpus"
        rendered = render_pov_card(stored)
        assert "Your corpus" in rendered and "[values]" in rendered
