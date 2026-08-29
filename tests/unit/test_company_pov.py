"""Company themes (RFC-016): the POV pipeline pointed at a company's document pool."""

import json
from pathlib import Path

import pytest

import wingman.application.people as people_module
from wingman.application.ingest import IngestError
from wingman.application.people import add_person, attach_feed, fetch_person_feed
from wingman.application.pov import build_company_pov, company_card_id, render_pov_card
from wingman.domain.person import FeedAttribution, FeedKind, FeedSource
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelRequest, ModelResponse

PERSON_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><item>
    <title>Explainable Analytics</title>
    <link>https://jane.substack.com/p/explainable</link>
    <content:encoded><![CDATA[<p>Every answer should show its work.</p>]]></content:encoded>
  </item></channel></rss>
"""

ORG_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><item>
    <title>Ship Small</title>
    <link>https://acme.example.com/blog/ship-small</link>
    <content:encoded><![CDATA[<p>We ship small changes every day.</p>]]></content:encoded>
  </item></channel></rss>
"""


class ScriptedProvider:
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


def _seed_company(storage: Storage, config) -> tuple[str, str]:
    """Jane (employee, own substack) + an Acme org feed on outsider Bo.

    Returns (jane_doc_id, org_doc_id)."""
    jane, _ = add_person(
        "Jane Author", storage, substack_url="https://jane.substack.com", company="Acme"
    )
    fetch_person_feed(jane, config, storage, fetcher=lambda url: PERSON_FEED.encode())
    bo, _ = add_person("Bo Outsider", storage, company="Elsewhere")
    bo = attach_feed(
        bo,
        FeedSource(
            url="https://acme.example.com/feed.xml",
            kind=FeedKind.RSS,
            attribution=FeedAttribution.ORGANIZATION,
            org_name="Acme",
        ),
        storage,
    )
    fetch_person_feed(bo, config, storage, fetcher=lambda url: ORG_FEED.encode())
    jane_doc = storage.list_external_documents(jane.person_id)[0].doc_id
    org_doc = storage.list_external_documents(bo.person_id)[0].doc_id
    return jane_doc, org_doc


def test_company_pool_and_attribution(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        jane_doc, org_doc = _seed_company(storage, config)
        provider = ScriptedProvider(
            {
                "stances": [
                    {
                        "statement": "Acme's people argue for explainable answers.",
                        "quote": "Every answer should show its work",
                        "doc_id": jane_doc,
                        "dimension": "technical",
                    },
                    {
                        "statement": "Acme ships in small increments.",
                        "quote": "We ship small changes every day",
                        "doc_id": org_doc,
                        "dimension": "attitude",
                    },
                ],
                "topics": ["explainability", "shipping"],
            }
        )
        report = build_company_pov("acme", storage, provider)
        # both pool branches reached the prompt, with authors attributed
        assert provider.last_prompt is not None
        assert "— by Jane Author" in provider.last_prompt
        assert "— by Acme" in provider.last_prompt  # org-attributed doc
        card = report.card
        assert card.person_id == company_card_id("acme")
        assert card.person_name == "Acme (company)"
        assert {stance.doc_title for stance in card.stances} == {
            "Explainable Analytics — by Jane Author",
            "Ship Small — by Acme",
        }
        # stored: retrievable without a model call, renderable
        stored = storage.get_pov_card(company_card_id("acme"))
        assert stored is not None
        assert "Acme (company)" in render_pov_card(stored)


def test_fabricated_quote_is_rejected(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        jane_doc, _ = _seed_company(storage, config)
        provider = ScriptedProvider(
            {
                "stances": [
                    {
                        "statement": "Acme loves blockchain.",
                        "quote": "we love blockchain",  # appears nowhere
                        "doc_id": jane_doc,
                        "dimension": "strategy",
                    }
                ],
                "topics": [],
            }
        )
        with pytest.raises(IngestError, match="no theme survived"):
            build_company_pov("Acme", storage, provider)
        assert storage.get_pov_card(company_card_id("acme")) is None


def test_company_pov_requires_documents(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Solo", storage, company="Ghost Co")
        with pytest.raises(IngestError, match="attributable"):
            build_company_pov("Ghost Co", storage, ScriptedProvider({"stances": [], "topics": []}))
        with pytest.raises(IngestError, match="empty"):
            build_company_pov("   ", storage, ScriptedProvider({"stances": [], "topics": []}))


def test_dossier_renders_company_themes(workspace: Path) -> None:
    from wingman.application.dossier import build_company_dossier

    config = load_config()
    with Storage(config.db_path) as storage:
        jane_doc, _ = _seed_company(storage, config)
        before = build_company_dossier("Acme", config, storage).markdown
        assert "no synthesized company themes" in before  # gap named
        provider = ScriptedProvider(
            {
                "stances": [
                    {
                        "statement": "Acme's people argue for explainable answers.",
                        "quote": "Every answer should show its work",
                        "doc_id": jane_doc,
                        "dimension": "technical",
                    }
                ],
                "topics": ["explainability"],
            }
        )
        build_company_pov("Acme", storage, provider)
        after = build_company_dossier("Acme", config, storage).markdown
        assert "## Company themes (synthesized" in after
        assert "[inference] [technical] Acme's people argue for explainable answers." in after
        assert '[fact] "Every answer should show its work"' in after
        assert "no synthesized company themes" not in after
