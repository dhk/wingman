"""Company dossiers: deterministic, dated, labeled composition of validated data."""

import json
from pathlib import Path

import pytest

from wingman.application.corpus import add_to_corpus
from wingman.application.dossier import DossierReport, build_company_dossier
from wingman.application.ingest import IngestError
from wingman.application.people import add_person, attach_feed, fetch_person_feed
from wingman.application.pov import build_pov_card
from wingman.application.similarity import embed_missing
from wingman.domain.person import FeedAttribution, FeedKind, FeedSource
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelRequest, ModelResponse
from wingman.providers.embeddings import HashedEmbeddingProvider

FEED_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><item>
    <title>{title}</title>
    <link>https://example.substack.com/p/{slug}</link>
    <pubDate>Mon, 06 Jan 2020 10:00:00 +0000</pubDate>
    <content:encoded><![CDATA[<p>{body}</p>]]></content:encoded>
  </item></channel></rss>
"""


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


def add_employee(storage: Storage, config, name: str, slug: str, company: str, body: str) -> None:
    person, _ = add_person(
        name, storage, substack_url=f"https://{slug}.substack.com", company=company
    )
    fetch_person_feed(
        person,
        config,
        storage,
        fetcher=lambda url: FEED_TEMPLATE.format(
            title=f"{name} writes", slug=slug, body=body
        ).encode(),
    )


def test_dossier_composes_people_stances_signals_and_gaps(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_employee(storage, config, "Ana", "ana", "DataCo", "kafka streaming data pipelines")
        add_employee(storage, config, "Bo", "bo", "DataCo", "streaming kafka analytics")
        add_employee(storage, config, "Cy", "cy", "StreamCorp", "kafka pipelines and analytics")
        ana = storage.find_person_by_name_key("ana")
        assert ana is not None
        doc_id = storage.list_external_documents(ana.person_id)[0].doc_id
        build_pov_card(
            "Ana",
            storage,
            ScriptedProvider(
                {
                    "stances": [
                        {
                            "statement": "Believes streaming beats batch.",
                            "quote": "kafka streaming data pipelines",
                            "doc_id": doc_id,
                        }
                    ],
                    "topics": ["streaming"],
                }
            ),
        )
        essay = workspace / "essay.md"
        essay.write_text("kafka streaming pipelines", encoding="utf-8")
        add_to_corpus(essay, "writing", config, storage)
        embed_missing(storage, HashedEmbeddingProvider())

        report = build_company_dossier("dataco", config, storage)
        assert report.company == "DataCo"
        assert Path(report.path).exists()
        assert Path(report.path).read_text(encoding="utf-8") == report.markdown
        assert "companies" in report.path and "dataco-" in report.path

        text = report.markdown
        assert "# Company dossier: DataCo" in text
        assert "Ana" in text and "Bo" in text and "Cy" not in text.split("Signals")[0]
        assert "[inference] Believes streaming beats batch." in text
        assert '[fact] "kafka streaming data pipelines"' in text
        assert "Alignment with your corpus: 0." in text
        assert "StreamCorp" in text  # similar-company signal
        assert "⚠ stale" in text  # 2020 feed date is long past STALE_AFTER_DAYS
        assert "POV cards missing for: Bo" in text


def test_dossier_degrades_without_embeddings_or_cards(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_employee(storage, config, "Ana", "ana", "DataCo", "kafka streaming")
        report = build_company_dossier("DataCo", config, storage)
        text = report.markdown
        assert "No POV cards yet" in text
        assert "Alignment with your corpus: unavailable" in text


def test_dossier_from_org_attributed_docs_only(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        person, _ = add_person("Scott", storage)  # no company field
        attach_feed(
            person,
            FeedSource(
                url="https://firm.example.com/insights",
                kind=FeedKind.INDEX_PAGE,
                attribution=FeedAttribution.ORGANIZATION,
                org_name="Example VC",
            ),
            storage,
        )
        person = storage.find_person_by_name_key("scott")
        assert person is not None

        def fetch(url: str) -> bytes:
            if url.endswith("/insights"):
                return b'<html><a href="/insights/post-one">one</a></html>'
            return b"<html><body><p>research driven investing</p></body></html>"

        fetch_person_feed(person, config, storage, fetcher=fetch)
        report = build_company_dossier("Example VC", config, storage)
        text = report.markdown
        assert "# Company dossier: Example VC" in text
        assert "none — documents come from org-attributed feeds only" in text
        # the org feed is listed even though Scott's own company isn't Example VC
        assert "https://firm.example.com/insights (index_page, via Scott)" in text
        assert "undated" in text  # index-page docs carry no publication date


def test_dossier_is_gaps_only_for_unknown_company(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        report = build_company_dossier("NoSuchCo", config, storage)
        assert isinstance(report, DossierReport)
        assert report.company == "NoSuchCo"
        text = report.markdown
        assert "# Company dossier: NoSuchCo" in text
        assert "## Gaps" in text
        assert (
            "no watched people or attributed feeds for this company — attach one with "
            '\'wingman people add "<name>" --company "NoSuchCo"\' or '
            "'wingman company add-source \"NoSuchCo\" <https-url>'"
        ) in text


def test_dossier_filename_is_safe_and_blank_names_are_rejected(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        # a company name with path separators must not escape reports/companies/
        add_employee(storage, config, "Ana", "ana", "Acme / ../Widgets", "kafka streaming")
        report = build_company_dossier("Acme / ../Widgets", config, storage)
        written = Path(report.path).resolve()
        companies_dir = (config.reports_dir / "companies").resolve()
        assert written.parent == companies_dir
        assert written.name.startswith("acme-widgets-")

        # a blank name must not silently match everyone with no company set
        add_person("No Company", storage)
        with pytest.raises(IngestError, match="empty"):
            build_company_dossier("   ", config, storage)
