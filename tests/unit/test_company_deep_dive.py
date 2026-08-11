"""Company deep-dive: the spend gate, the source gates, and gated storage (#350).

Every test here is a regression test for one of the three promises the
feature makes — confirm before spending, store nothing before approval, and
never store a claim whose source cannot be opened.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from wingman.agents.profile_curator import ProposalParseError
from wingman.application.company_deep_dive import (
    SEARCH_FEE_USD,
    citation_urls,
    parse_rendered_findings,
    render_company_dossier,
    render_findings,
    research_company_dossier,
    review_findings,
    save_company_dossier,
    spend_warning,
)
from wingman.application.ingest import IngestError
from wingman.domain.company import CompanyDimension, CompanyFinding
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelRequest, ModelResponse

FINDINGS_JSON = """{
  "findings": [
    {"dimension": "market_position",
     "claim": "Acme is the second-largest widget vendor in EMEA.",
     "quote": "",
     "source_url": "https://acme.example/investors/annual-2026",
     "source_title": "Acme 2026 annual report"},
    {"dimension": "values",
     "claim": "Acme states that safety comes before shipping speed.",
     "quote": "we will delay a launch rather than ship an unsafe product",
     "source_url": "https://acme.example/values",
     "source_title": "Our values"},
    {"dimension": "culture",
     "claim": "Acme cut 12% of staff in March 2026.",
     "quote": "",
     "source_url": "https://news.example/acme-layoffs",
     "source_title": "Acme lays off 12%"}
  ]
}"""

SOURCES_BLOCK = """

Sources:
- [Acme 2026 annual report](https://acme.example/investors/annual-2026)
- [Our values](https://acme.example/values/)
- [Acme lays off 12%](https://news.example/acme-layoffs)
"""


class ScriptedProvider:
    """Returns canned text; records the request. Never touches the network."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.last_request: ModelRequest | None = None
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        self.last_request = request
        return ModelResponse(
            text=self._text, provider="openrouter", model="scripted-1", latency_ms=0
        )


def _response(text: str) -> ModelResponse:
    return ModelResponse(text=text, provider="openrouter", model="scripted-1", latency_ms=0)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return tmp_path


# --- the spend gate -------------------------------------------------------


def test_spend_warning_states_the_cost_and_costs_nothing_to_produce() -> None:
    """'Confirm before spending' is only meaningful if the confirmation says
    what will be searched and what it costs — and if producing it is free."""
    warning = spend_warning("Acme Corp")
    assert "Acme Corp" in warning
    assert f"${SEARCH_FEE_USD:.2f}" in warning
    assert "market position" in warning and "values" in warning and "culture" in warning
    assert "Nothing has been searched or stored yet." in warning


def test_research_is_one_bounded_call_with_the_company_in_the_prompt() -> None:
    provider = ScriptedProvider(FINDINGS_JSON + SOURCES_BLOCK)
    research_company_dossier("Acme Corp", provider)
    assert provider.calls == 1  # one shot, not a crawl (RFC-009)
    assert provider.last_request is not None
    assert "Acme Corp" in provider.last_request.prompt
    assert provider.last_request.max_tokens > ModelRequest.model_fields["max_tokens"].default


def test_research_rejects_a_blank_company_name() -> None:
    provider = ScriptedProvider("irrelevant")
    with pytest.raises(IngestError, match="company name"):
        research_company_dossier("   ", provider)
    assert provider.calls == 0


# --- the research-time source gate ----------------------------------------


def test_citation_urls_come_from_the_providers_own_sources_block() -> None:
    urls = citation_urls(
        "Body text.\n\nSources:\n- [A](https://a.example/x/)\n- [B](https://b.example/y)"
    )
    assert urls == {"https://a.example/x", "https://b.example/y"}


def test_a_citation_matches_the_same_page_spelled_differently() -> None:
    """#348's rule, reused: www vs bare and a trailing slash are one page,
    so a good finding is not refused over cosmetics."""
    payload = """{"findings": [
      {"dimension": "values", "claim": "Acme states that safety comes first.",
       "source_url": "https://acme.example/values"}
    ]}"""
    sources = "\n\nSources:\n- [Our values](https://www.acme.example/values/)\n"
    review = review_findings("Acme Corp", _response(payload + sources))
    assert len(review.findings) == 1


def test_findings_whose_url_the_search_returned_are_kept() -> None:
    review = review_findings("Acme Corp", _response(FINDINGS_JSON + SOURCES_BLOCK))
    assert [finding.dimension for finding in review.findings] == [
        CompanyDimension.MARKET_POSITION,
        CompanyDimension.VALUES,
        CompanyDimension.CULTURE,
    ]
    assert review.rejected == []
    values = review.findings[1]
    assert values.quote == "we will delay a launch rather than ship an unsafe product"
    # A trailing slash in the citation is the same page as one without it.
    assert values.source_url == "https://acme.example/values"


def test_a_finding_citing_a_page_the_search_never_returned_is_refused() -> None:
    """The failure this prevents: a web-grounded model states a plausible
    claim and attaches a plausible URL it never fetched. The provider's own
    annotation array is the only retrieval evidence in the response, so a
    citation absent from it is treated as unverifiable and dropped."""
    payload = """{"findings": [
      {"dimension": "culture", "claim": "Acme has a four-day week.",
       "source_url": "https://acme.example/careers/four-day-week"}
    ]}"""
    review = review_findings("Acme Corp", _response(payload + SOURCES_BLOCK))
    assert review.findings == []
    assert len(review.rejected) == 1
    assert "not among the pages the search returned" in review.rejected[0].reason


def test_every_finding_is_refused_when_the_provider_returned_no_citations() -> None:
    """A response with no citation metadata at all — the search plugin
    failed, or a non-search provider was configured — is exactly the case
    where confident prose is most dangerous. Nothing survives."""
    review = review_findings("Acme Corp", _response(FINDINGS_JSON))
    assert review.findings == []
    assert len(review.rejected) == 3
    assert all("no citations at all" in item.reason for item in review.rejected)


def test_a_finding_on_an_unknown_dimension_is_refused_by_name() -> None:
    payload = """{"findings": [
      {"dimension": "vibes", "claim": "Acme feels nice.",
       "source_url": "https://acme.example/values"}
    ]}"""
    review = review_findings("Acme Corp", _response(payload + SOURCES_BLOCK))
    assert review.findings == []
    assert "vibes" in review.rejected[0].reason


def test_a_response_that_is_not_the_agreed_schema_fails_visibly() -> None:
    with pytest.raises(ProposalParseError):
        review_findings("Acme Corp", _response("I could not find anything about Acme."))


def test_a_finding_cannot_be_constructed_without_a_fetchable_source() -> None:
    """The domain refuses it, so no future caller can store an unsourced
    claim by forgetting to check — structure, not vigilance."""
    for bad in ("", "acme.example/values", "the company website", "ftp://acme.example/v"):
        with pytest.raises(ValidationError):
            CompanyFinding(
                dimension=CompanyDimension.VALUES, claim="Acme values safety.", source_url=bad
            )


# --- preview is what gets stored ------------------------------------------


def test_the_rendered_preview_round_trips_into_the_same_findings() -> None:
    """What the user reads and what the save path parses are one string, so
    'approved-then-silently-different' is impossible by construction."""
    review = review_findings("Acme Corp", _response(FINDINGS_JSON + SOURCES_BLOCK))
    content = render_findings(review)
    parsed = parse_rendered_findings(content)
    assert parsed.findings == review.findings
    assert parsed.provider == "openrouter"
    assert parsed.model == "scripted-1"


def test_the_rejected_block_in_the_preview_cannot_come_back_in() -> None:
    payload = """{"findings": [
      {"dimension": "culture", "claim": "Acme has a four-day week.",
       "source_url": "https://invented.example/four-day-week"},
      {"dimension": "values", "claim": "Acme states that safety comes first.",
       "source_url": "https://acme.example/values"}
    ]}"""
    review = review_findings("Acme Corp", _response(payload + SOURCES_BLOCK))
    content = render_findings(review)
    assert "four-day week" in content  # the user is told what was dropped
    parsed = parse_rendered_findings(content)
    assert [finding.claim for finding in parsed.findings] == [
        "Acme states that safety comes first."
    ]


# --- the save-time source gate --------------------------------------------


def test_save_stores_the_reviewed_findings(workspace: Path) -> None:
    config = load_config()
    review = review_findings("Acme Corp", _response(FINDINGS_JSON + SOURCES_BLOCK))
    with Storage(config.db_path) as storage:
        dossier = save_company_dossier("Acme Corp", render_findings(review), storage)
        assert len(dossier.findings) == 3
        stored = storage.get_company_dossier("acme corp")
        assert stored is not None
        assert stored.provider == "openrouter"
        assert "second-largest widget vendor" in render_company_dossier(stored)


def test_save_refuses_content_whose_findings_carry_no_source(workspace: Path) -> None:
    """The second gate: hand-edited or hand-written content reaches storage
    through the same door, and an unsourced claim does not get through it."""
    config = load_config()
    content = (
        "# Company deep-dive: Acme Corp\n\n"
        "## Market position\n\n"
        "- claim: Acme is the biggest widget vendor in the world.\n"
    )
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="verifiable source"):
            save_company_dossier("Acme Corp", content, storage)
        assert storage.get_company_dossier("acme corp") is None


def test_save_drops_only_the_unsourced_finding_when_others_are_sourced(workspace: Path) -> None:
    config = load_config()
    content = (
        "# Company deep-dive: Acme Corp\n\n"
        "## Market position\n\n"
        "- claim: Acme is the biggest widget vendor in the world.\n\n"
        "## Stated values\n\n"
        "- claim: Acme states that safety comes first.\n"
        "  source: [Our values](https://acme.example/values)\n"
    )
    with Storage(config.db_path) as storage:
        dossier = save_company_dossier("Acme Corp", content, storage)
        assert [finding.claim for finding in dossier.findings] == [
            "Acme states that safety comes first."
        ]


def test_save_rejects_a_blank_company_name(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="company name"):
            save_company_dossier("   ", "irrelevant", storage)


def test_save_overwrites_rather_than_versioning(workspace: Path) -> None:
    config = load_config()
    first = "## Stated values\n\n- claim: First pass.\n  source: [v](https://acme.example/values)\n"
    second = (
        "## Stated values\n\n- claim: Second, better pass.\n"
        "  source: [v](https://acme.example/values)\n"
    )
    with Storage(config.db_path) as storage:
        save_company_dossier("Acme Corp", first, storage)
        save_company_dossier("Acme Corp", second, storage)
        stored = storage.get_company_dossier("acme corp")
        assert stored is not None
        assert [finding.claim for finding in stored.findings] == ["Second, better pass."]


# --- lifecycle ------------------------------------------------------------


def _store_one(storage: Storage, name: str) -> None:
    save_company_dossier(
        name,
        "## Stated values\n\n- claim: Acme states that safety comes first.\n"
        "  source: [Our values](https://acme.example/values)\n",
        storage,
    )


def test_renaming_a_company_carries_its_deep_dive_along(workspace: Path) -> None:
    from wingman.application.research import rename_company

    config = load_config()
    with Storage(config.db_path) as storage:
        _store_one(storage, "Acme Corp")
        rename_company("Acme Corp", "Acme Industries", storage)
        assert storage.get_company_dossier("acme corp") is None
        moved = storage.get_company_dossier("acme industries")
        assert moved is not None
        assert moved.company_name == "Acme Industries"


def test_deleting_a_company_deletes_its_deep_dive(workspace: Path) -> None:
    from wingman.application.research import delete_company

    config = load_config()
    with Storage(config.db_path) as storage:
        _store_one(storage, "Acme Corp")
        existed, _ = delete_company("Acme Corp", storage)
        assert existed is True
        assert storage.get_company_dossier("acme corp") is None


def test_the_company_dossier_renders_the_deep_dive_and_names_it_as_a_gap(
    workspace: Path,
) -> None:
    from wingman.application.dossier import build_company_dossier

    config = load_config()
    with Storage(config.db_path) as storage:
        before = build_company_dossier("Acme Corp", config, storage)
        assert "no open-web deep dive" in before.markdown
        _store_one(storage, "Acme Corp")
        after = build_company_dossier("Acme Corp", config, storage)
        assert "## Open-web deep dive" in after.markdown
        assert "[inference] Acme states that safety comes first." in after.markdown
        assert "https://acme.example/values" in after.markdown
        assert "no open-web deep dive" not in after.markdown
