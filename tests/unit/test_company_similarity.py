"""Company similarity: arithmetic over people-by-company and org-attributed writing."""

from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.people import add_person, attach_feed, fetch_person_feed
from wingman.application.similarity import companies_like, embed_missing, similar_companies
from wingman.domain.person import FeedAttribution, FeedKind, FeedSource
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.embeddings import HashedEmbeddingProvider

FEED_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><item>
    <title>{title}</title>
    <link>https://example.substack.com/p/{slug}</link>
    <content:encoded><![CDATA[<p>{body}</p>]]></content:encoded>
  </item></channel></rss>
"""


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


def test_company_vectors_group_people_and_rank(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_employee(storage, config, "Ana", "ana", "DataCo", "kafka streaming data pipelines")
        add_employee(storage, config, "Bo", "bo", "DataCo", "streaming kafka analytics")
        add_employee(storage, config, "Cy", "cy", "StreamCorp", "kafka pipelines and analytics")
        add_employee(storage, config, "Di", "di", "GreenCo", "roses tulips compost pruning")
        embed_missing(storage, HashedEmbeddingProvider())

        report = similar_companies(storage, name="DataCo")
        names = [entry.name for entry in report.companies]
        assert names[0] == "StreamCorp"
        assert names[-1] == "GreenCo"
        assert "DataCo" not in names  # reference excluded
        data_signal = similar_companies(storage, name="StreamCorp").companies
        dataco = next(entry for entry in data_signal if entry.name == "DataCo")
        assert dataco.people == 2 and dataco.documents == 2


def test_org_attributed_documents_count_toward_the_org(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        # Scott works at "Example VC" per LinkedIn, and the firm blog is org-attributed
        person, _ = add_person("Scott", storage, company="Example VC")
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
            return b"<html><body><p>research driven ideation and investing</p></body></html>"

        fetch_person_feed(person, config, storage, fetcher=fetch)
        add_employee(storage, config, "Vi", "vi", "OtherFund", "ideation and investing research")
        embed_missing(storage, HashedEmbeddingProvider())

        report = similar_companies(storage, name="Example VC")
        assert [entry.name for entry in report.companies] == ["OtherFund"]
        # the org signal deduplicates: person.company == doc.organization → one doc
        other = similar_companies(storage, name="OtherFund").companies
        example = next(entry for entry in other if entry.name == "Example VC")
        assert example.documents == 1 and example.people == 1


def test_similar_to_own_corpus_and_like_centroid(workspace: Path) -> None:
    from wingman.application.corpus import add_to_corpus

    config = load_config()
    with Storage(config.db_path) as storage:
        essay = workspace / "essay.md"
        essay.write_text("kafka streaming pipelines", encoding="utf-8")
        add_to_corpus(essay, "writing", config, storage)
        add_employee(storage, config, "Ana", "ana", "DataCo", "kafka streaming pipelines")
        add_employee(storage, config, "Di", "di", "GreenCo", "roses tulips compost")
        add_employee(storage, config, "Ed", "ed", "BridgeCo", "kafka pipelines and tulips")
        embed_missing(storage, HashedEmbeddingProvider())

        mine = similar_companies(storage)
        assert mine.reference == "your corpus"
        assert mine.companies[0].name == "DataCo"

        blend = companies_like(storage, names=["DataCo", "GreenCo"])
        assert blend.reference == "DataCo + GreenCo"
        assert blend.companies[0].name == "BridgeCo"
        assert {entry.name for entry in blend.companies} == {"BridgeCo"}


def test_company_similarity_fails_visibly(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="no company has embedded writing"):
            similar_companies(storage)
        add_employee(storage, config, "Ana", "ana", "DataCo", "kafka streaming")
        embed_missing(storage, HashedEmbeddingProvider())
        with pytest.raises(IngestError, match="attributable"):
            similar_companies(storage, name="NoSuchCo")
        with pytest.raises(IngestError, match="at least two"):
            companies_like(storage, names=["DataCo"])
