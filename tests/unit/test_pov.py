"""POV cards: model proposes, deterministic validation disposes."""

import json
from pathlib import Path

import pytest

import wingman.application.people as people_module
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
    # add_person verifies a passed substack_url's feed before storing it
    # (#483) -- a default valid feed so that verification never hits the
    # real network; tests that care about specific feed content patch
    # fetch_url again afterward.
    monkeypatch.setattr(
        people_module,
        "fetch_url",
        lambda url: (
            b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'
        ),
    )
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


def test_own_pov_builds_from_interview_captures_with_no_corpus(workspace: Path) -> None:
    """Profile Bootstrap's whole point (docs/PROFILE-BOOTSTRAP-DESIGN.md):
    someone with zero published writing still gets a real stance, built
    from captured interview reactions alone."""
    from wingman.application.interview import capture_interview_reaction
    from wingman.application.pov import CORPUS_PERSON_ID, build_own_pov
    from wingman.infrastructure.config import load_config

    config = load_config()
    with Storage(config.db_path) as storage:
        capture_interview_reaction(
            "values_pro",
            "Jane Goodall",
            "She spent decades on one cause and never wavered.",
            config,
            storage,
        )
        items = storage.list_profile_items()
        doc_id = items[0].item_id  # InterviewDocument.doc_id == ProfileItem.item_id
        provider = ScriptedProvider(
            {
                "stances": [
                    {
                        "statement": "The author values sustained, unwavering commitment.",
                        "quote": "never wavered",
                        "doc_id": doc_id,
                        "dimension": "values",
                    }
                ],
                "topics": ["persistence"],
            }
        )
        report = build_own_pov(storage, provider)
        assert len(report.card.stances) == 1
        stored = storage.get_pov_card(CORPUS_PERSON_ID)
        assert stored is not None
        assert stored.stances[0].quote == "never wavered"


def test_own_pov_merges_corpus_and_interview_documents(workspace: Path) -> None:
    from wingman.application.corpus import add_to_corpus
    from wingman.application.interview import capture_interview_reaction
    from wingman.application.pov import build_own_pov
    from wingman.infrastructure.config import load_config

    config = load_config()
    with Storage(config.db_path) as storage:
        essay = workspace / "essay.md"
        essay.write_text("Answers must show their work.", encoding="utf-8")
        add_to_corpus(essay, "writing", config, storage)
        corpus_doc_id = storage.list_corpus_documents()[0].doc_id
        capture_interview_reaction(
            "values_pro", "Jane Goodall", "She never wavered on one cause.", config, storage
        )
        interview_doc_id = storage.list_profile_items()[0].item_id

        provider = ScriptedProvider(
            {
                "stances": [
                    {
                        "statement": "The author believes answers must show their work.",
                        "quote": "show their work",
                        "doc_id": corpus_doc_id,
                    },
                    {
                        "statement": "The author values sustained commitment.",
                        "quote": "never wavered",
                        "doc_id": interview_doc_id,
                    },
                ],
                "topics": [],
            }
        )
        report = build_own_pov(storage, provider)
        assert len(report.card.stances) == 2
        quotes = {stance.quote for stance in report.card.stances}
        assert quotes == {"show their work", "never wavered"}


def test_own_pov_with_persona_excludes_corpus_and_uses_only_their_captures(
    workspace: Path,
) -> None:
    """Coaching mode (docs/COACHING-MODE-DESIGN.md): "corpus is shared but
    never automatic evidence" — a persona's synthesized stance never sees
    the coach's own writing, only their own scoped interview captures."""
    from wingman.application.coaching import find_or_create_persona
    from wingman.application.corpus import add_to_corpus
    from wingman.application.interview import capture_interview_reaction
    from wingman.application.pov import CORPUS_PERSON_ID, build_own_pov, persona_card_id

    config = load_config()
    with Storage(config.db_path) as storage:
        essay = workspace / "essay.md"
        essay.write_text("Coach's own essay text, never Mike's stance.", encoding="utf-8")
        add_to_corpus(essay, "writing", config, storage)

        persona = find_or_create_persona("Mike Chen", storage)
        capture_interview_reaction(
            "values_pro",
            "Jane Goodall",
            "Mike never wavered on one cause.",
            config,
            storage,
            persona_id=persona.persona_id,
        )
        mike_doc_id = next(
            item for item in storage.list_profile_items() if item.persona_id == persona.persona_id
        ).item_id

        provider = ScriptedProvider(
            {
                "stances": [
                    {
                        "statement": "Mike values sustained commitment.",
                        "quote": "never wavered",
                        "doc_id": mike_doc_id,
                        "dimension": "values",
                    }
                ],
                "topics": ["persistence"],
            }
        )
        report = build_own_pov(storage, provider, persona=persona)
        assert provider.last_prompt is not None
        assert "Coach's own essay" not in provider.last_prompt
        assert len(report.card.stances) == 1

        stored = storage.get_pov_card(persona_card_id(persona.persona_id))
        assert stored is not None
        assert stored.person_name == "Mike Chen"
        # the coach's own corpus card, if it exists, is untouched/separate
        assert storage.get_pov_card(CORPUS_PERSON_ID) is None


def test_own_pov_with_persona_error_message_is_persona_specific(workspace: Path) -> None:
    from wingman.application.coaching import find_or_create_persona
    from wingman.application.pov import build_own_pov

    config = load_config()
    with Storage(config.db_path) as storage:
        persona = find_or_create_persona("Mike Chen", storage)
        with pytest.raises(IngestError, match="nothing has been captured for Mike Chen"):
            build_own_pov(storage, ScriptedProvider({"stances": [], "topics": []}), persona=persona)


def test_own_pov_with_persona_ignores_coachs_own_interview_captures(workspace: Path) -> None:
    """Only the persona's OWN captures feed their stance — not the coach's
    unscoped ones, even though both live in the same interview mechanic."""
    from wingman.application.coaching import find_or_create_persona
    from wingman.application.interview import capture_interview_reaction
    from wingman.application.pov import build_own_pov

    config = load_config()
    with Storage(config.db_path) as storage:
        capture_interview_reaction(
            "values_pro", "Jane Goodall", "The coach's own reason.", config, storage
        )
        persona = find_or_create_persona("Mike Chen", storage)
        with pytest.raises(IngestError, match="nothing has been captured for Mike Chen"):
            build_own_pov(storage, ScriptedProvider({"stances": [], "topics": []}), persona=persona)


def test_mcp_my_pov_reads_the_active_personas_own_stored_card(workspace: Path) -> None:
    """my_pov's stored-card path (no model call) reads whichever persona is
    active, under their own card_id — never the coach's CORPUS_PERSON_ID
    card, even if one exists."""
    from wingman.application.coaching import find_or_create_persona
    from wingman.application.pov import CORPUS_PERSON_ID, CORPUS_PERSON_NAME, persona_card_id
    from wingman.domain.pov import PovCard
    from wingman.mcp_server import coach_persona, my_pov

    config = load_config()
    with Storage(config.db_path) as storage:
        persona = find_or_create_persona("Mike Chen", storage)
        own_card = PovCard(
            person_id=CORPUS_PERSON_ID,
            person_name=CORPUS_PERSON_NAME,
            documents_used=1,
            provider="scripted",
            model="scripted-1",
            prompt_version="v0",
        )
        storage.save_pov_card(own_card)
        mike_card = PovCard(
            person_id=persona_card_id(persona.persona_id),
            person_name="Mike Chen",
            documents_used=1,
            provider="scripted",
            model="scripted-1",
            prompt_version="v0",
        )
        storage.save_pov_card(mike_card)

    coach_persona("set", "Mike Chen")
    result = my_pov(refresh=False)
    assert result.startswith("Acting as: coach for Mike Chen.")
    assert "Mike Chen" in result
    assert "Your corpus" not in result

    coach_persona("clear")
    own_result = my_pov(refresh=False)
    assert own_result.startswith("Acting as: yourself.")
    assert "Your corpus" in own_result


def test_cli_pov_reads_the_active_personas_own_stored_card(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from wingman.application.coaching import find_or_create_persona
    from wingman.application.pov import persona_card_id
    from wingman.cli.main import app
    from wingman.domain.pov import PovCard
    from wingman.infrastructure.config import ENV_DATA_DIR

    config = load_config()
    with Storage(config.db_path) as storage:
        persona = find_or_create_persona("Mike Chen", storage)
        storage.save_pov_card(
            PovCard(
                person_id=persona_card_id(persona.persona_id),
                person_name="Mike Chen",
                documents_used=1,
                provider="scripted",
                model="scripted-1",
                prompt_version="v0",
            )
        )

    monkeypatch.setenv(ENV_DATA_DIR, str(config.data_dir))
    runner = CliRunner()
    runner.invoke(app, ["coach-persona", "set", "Mike Chen"])
    result = runner.invoke(app, ["pov"])
    assert result.exit_code == 0
    assert "Acting as: coach for Mike Chen." in result.output
    assert "Mike Chen" in result.output


def test_own_pov_never_sees_the_stimulus_only_the_users_own_why(workspace: Path) -> None:
    """The security property discussed for this slice: the stimulus's own
    fetched content never reaches the prompt at all (interview.py discards
    it at capture time) — only the user's verbatim 'why' does, and a
    fabricated quote lifted from anywhere else is rejected exactly like any
    other document type's fabrication guard."""
    from wingman.application.interview import capture_interview_reaction
    from wingman.application.pov import build_own_pov
    from wingman.infrastructure.config import load_config

    config = load_config()
    with Storage(config.db_path) as storage:
        page = (
            b"<html><head><title>An Essay</title></head>"
            b"<body><p>SECRET_STIMULUS_SENTENCE never typed by the user.</p></body></html>"
        )
        capture_interview_reaction(
            "alignment_of_perspective_agree",
            "https://example.com/essay",
            "This matches how I already think about it.",
            config,
            storage,
            fetcher=lambda url: page,
        )
        doc_id = storage.list_profile_items()[0].item_id

        # honest attempt: quoting the user's own 'why' validates fine
        honest = ScriptedProvider(
            {
                "stances": [
                    {
                        "statement": "The author agreed with the piece.",
                        "quote": "already think about it",
                        "doc_id": doc_id,
                    }
                ],
                "topics": [],
            }
        )
        report = build_own_pov(storage, honest)
        assert len(report.card.stances) == 1
        assert honest.last_prompt is not None
        assert "SECRET_STIMULUS_SENTENCE" not in honest.last_prompt  # never reached the prompt

        # a fabricated quote lifted from the stimulus (which the model
        # cannot actually have seen, since it was never in the prompt) is
        # rejected the same way any fabricated quote is -- zero surviving
        # stances raises rather than silently storing an empty card
        dishonest = ScriptedProvider(
            {
                "stances": [
                    {
                        "statement": "Fabricated from content never given to the model.",
                        "quote": "SECRET_STIMULUS_SENTENCE",
                        "doc_id": doc_id,
                    }
                ],
                "topics": [],
            }
        )
        with pytest.raises(IngestError, match="no stance survived validation"):
            build_own_pov(storage, dishonest)


def test_own_pov_recent_undated_import_survives_large_dated_pool(workspace: Path) -> None:
    """#649: real file ingestion leaves published_at unset; search is not selection."""
    from datetime import UTC, datetime

    from wingman.application.corpus import add_to_corpus, find_evidence
    from wingman.application.interview import capture_interview_reaction
    from wingman.application.pov import build_own_pov
    from wingman.domain import SourceRecord
    from wingman.domain.corpus import CorpusDocument

    config = load_config()
    with Storage(config.db_path) as storage:
        for index in range(100):
            record = SourceRecord(
                source_type="writing", source_locator=f"old-{index}.md", content_hash=f"old-{index}"
            )
            storage.add_source_record(record)
            storage.add_corpus_document(
                CorpusDocument(
                    source_record_id=record.record_id,
                    source_type="writing",
                    title=f"Old essay {index}",
                    word_count=5,
                    published_at=datetime(2020, 1, 1, tzinfo=UTC),
                ),
                "Older writing has real evidence.",
            )
        capture_interview_reaction(
            "values_pro", "Example scientist", "Persistent curiosity matters.", config, storage
        )
        essay = workspace / "new-essay.md"
        essay.write_text("# Decision systems\nDecision systems must expose their evidence.")
        add_to_corpus(essay, "writing", config, storage)
        doc = find_evidence("Decision", storage)[0].document
        assert doc.published_at is None
        provider = ScriptedProvider(
            {
                "stances": [
                    {
                        "statement": "The author values inspectable decisions.",
                        "quote": "Decision systems must expose their evidence.",
                        "doc_id": doc.doc_id,
                    }
                ]
            }
        )
        report = build_own_pov(storage, provider)
        assert report.card.documents_used == 20
        assert provider.last_prompt and f"--- doc_id: {doc.doc_id}\n" in provider.last_prompt
        assert report.card.stances[0].doc_id == doc.doc_id


def test_own_pov_date_fallback_and_ties_are_deterministic() -> None:
    from datetime import UTC, datetime, timedelta, timezone

    from wingman.application.pov import _newest_mixed
    from wingman.domain.corpus import CorpusDocument
    from wingman.domain.interview import InterviewDocument

    def corpus(doc_id, published_at, added_at):
        return CorpusDocument(
            doc_id=doc_id,
            source_record_id=doc_id,
            source_type="writing",
            title=doc_id,
            word_count=1,
            published_at=published_at,
            added_at=added_at,
        )

    now = datetime(2026, 11, 8, tzinfo=UTC)
    a = corpus("a", None, now.replace(tzinfo=None))
    b = corpus("b", None, now.astimezone(timezone(timedelta(hours=-8))))
    old = corpus("old", now - timedelta(days=10), now + timedelta(days=1))
    capture = InterviewDocument(
        doc_id="capture",
        title="Reaction",
        body="My words",
        source_record_id="capture",
        published_at=now - timedelta(days=1),
    )
    undated = capture.model_copy(update={"doc_id": "undated", "published_at": None})
    expected = ["b", "a", "capture", "old", "undated"]
    for documents in ([a, b, old, capture, undated], [undated, capture, old, b, a]):
        assert [doc.doc_id for doc in _newest_mixed(documents, 20)] == expected


def test_own_pov_manifest_matches_prompt_and_survives_surface_reads(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import re

    from typer.testing import CliRunner

    import wingman.mcp_server as mcp_module
    from wingman.application.corpus import add_to_corpus
    from wingman.application.pov import CORPUS_PERSON_ID
    from wingman.cli.main import app
    from wingman.domain import SourceRecord
    from wingman.domain.corpus import CorpusDocument

    config = load_config()
    with Storage(config.db_path) as storage:
        for name in ("cited", "uncited"):
            essay = workspace / f"{name}.md"
            essay.write_text(f"# {name}\nEvidence should be inspectable.")
            add_to_corpus(essay, "writing", config, storage)
        record = SourceRecord(
            source_type="writing", source_locator="empty.md", content_hash="empty"
        )
        storage.add_source_record(record)
        empty = CorpusDocument(
            source_record_id=record.record_id, source_type="writing", title="empty", word_count=0
        )
        storage.add_corpus_document(empty, "   ")
        cited = next(doc for doc in storage.list_corpus_documents() if doc.title == "cited")
    provider = ScriptedProvider(
        {
            "stances": [
                {
                    "statement": "The author values evidence.",
                    "quote": "Evidence should be inspectable.",
                    "doc_id": cited.doc_id,
                }
            ]
        }
    )
    monkeypatch.setattr(mcp_module, "get_provider", lambda *args: provider)
    refreshed = mcp_module.my_pov(refresh=True)
    assert "Documents supplied to synthesis" in refreshed
    with Storage(config.db_path) as storage:
        card = storage.get_pov_card(CORPUS_PERSON_ID)
        assert card is not None
        supplied = card.documents_supplied
        assert supplied is not None
        assert [doc.doc_id for doc in supplied] == re.findall(
            r"^--- doc_id: (.+)$", provider.last_prompt, re.MULTILINE
        )
        assert {doc.title for doc in supplied} == {"cited", "uncited"}
        assert card.documents_used == len(supplied) == 2

    def unexpected_provider(*args):
        pytest.fail("Stored card read must not request a model provider")

    monkeypatch.setattr(mcp_module, "get_provider", unexpected_provider)
    assert "Documents supplied to synthesis" in mcp_module.my_pov()
    result = CliRunner().invoke(app, ["pov"])
    assert result.exit_code == 0
    for doc in supplied:
        assert doc.doc_id in result.output and doc.title in result.output


def test_legacy_pov_card_has_unknown_manifest(workspace: Path) -> None:
    from wingman.domain.pov import PovCard

    config = load_config()
    with Storage(config.db_path) as storage:
        card = PovCard.model_validate(
            {
                "person_id": "__corpus__",
                "person_name": "Your corpus",
                "documents_used": 20,
                "provider": "scripted",
                "model": "scripted-1",
                "prompt_version": "v0",
            }
        )
        storage.save_pov_card(card)
        loaded = storage.get_pov_card("__corpus__")
        assert loaded is not None
        assert loaded.documents_supplied is None
        assert "input list unavailable" in render_pov_card(loaded)
