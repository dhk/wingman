"""Similarity is deterministic arithmetic over stored vectors (RFC-010)."""

from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.people import add_person, fetch_person_feed
from wingman.application.similarity import embed_missing, people_like, similar_people
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.embeddings import HashedEmbeddingProvider

FEED_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <item>
      <title>{title}</title>
      <link>https://example.substack.com/p/{slug}</link>
      <content:encoded><![CDATA[<p>{body}</p>]]></content:encoded>
    </item>
  </channel>
</rss>
"""


def feed(title: str, slug: str, body: str) -> bytes:
    return FEED_TEMPLATE.format(title=title, slug=slug, body=body).encode("utf-8")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return tmp_path


def add_writer(storage: Storage, config, name: str, slug: str, body: str) -> None:
    person, _ = add_person(name, storage, substack_url=f"https://{slug}.substack.com")
    fetch_person_feed(
        person, config, storage, fetcher=lambda url: feed(f"{name} writes", slug, body)
    )


def test_hashed_provider_is_deterministic_and_normalized() -> None:
    provider = HashedEmbeddingProvider()
    first, second = provider.embed(["streaming kafka billing"] * 2, input_type="document")
    assert first == second
    assert abs(sum(v * v for v in first) - 1.0) < 1e-6


def test_embed_missing_covers_corpus_and_external(workspace: Path) -> None:
    from wingman.application.corpus import add_to_corpus

    config = load_config()
    provider = HashedEmbeddingProvider()
    with Storage(config.db_path) as storage:
        essay = workspace / "essay.md"
        essay.write_text("# Streaming\n\nKafka pipelines and event streaming.\n", encoding="utf-8")
        add_to_corpus(essay, "writing", config, storage)
        add_writer(storage, config, "Jane Author", "jane", "Kafka streaming pipelines everywhere")

        report = embed_missing(storage, provider)
        assert report.corpus_embedded == 1
        assert report.external_embedded == 1
        assert storage.count_embeddings() == 2

        again = embed_missing(storage, provider)
        assert again.corpus_embedded == 0 and again.external_embedded == 0
        assert again.already_embedded == 2


def test_similar_people_ranks_by_shared_vocabulary(workspace: Path) -> None:
    config = load_config()
    provider = HashedEmbeddingProvider()
    with Storage(config.db_path) as storage:
        add_writer(
            storage, config, "Kafka Twin", "twin", "Kafka event streaming and billing pipelines"
        )
        add_writer(
            storage, config, "Kafka Cousin", "cousin", "Some Kafka streaming with other topics"
        )
        add_writer(storage, config, "Gardener", "garden", "Roses tulips compost and pruning")
        embed_missing(storage, provider)

        report = similar_people(storage, name="Kafka Twin")
        names = [entry.name for entry in report.people]
        assert names[0] == "Kafka Cousin"
        assert names[-1] == "Gardener"
        assert report.people[0].score > report.people[-1].score
        # self is excluded from its own ranking
        assert "Kafka Twin" not in names


def test_similar_to_own_corpus(workspace: Path) -> None:
    from wingman.application.corpus import add_to_corpus

    config = load_config()
    provider = HashedEmbeddingProvider()
    with Storage(config.db_path) as storage:
        essay = workspace / "essay.md"
        essay.write_text("Kafka streaming pipelines and billing systems", encoding="utf-8")
        add_to_corpus(essay, "writing", config, storage)
        add_writer(storage, config, "Kafka Twin", "twin", "Kafka streaming billing pipelines")
        add_writer(storage, config, "Gardener", "garden", "Roses tulips compost pruning")
        embed_missing(storage, provider)

        report = similar_people(storage)
        assert report.reference == "your corpus"
        assert report.people[0].name == "Kafka Twin"


def test_people_like_centroid(workspace: Path) -> None:
    config = load_config()
    provider = HashedEmbeddingProvider()
    with Storage(config.db_path) as storage:
        add_writer(storage, config, "Mario Rossi", "mario", "simple tools simple software design")
        add_writer(storage, config, "Brian Chen", "brian", "data notebooks and tools for analysis")
        add_writer(storage, config, "Bridge Person", "bridge", "simple tools for data analysis")
        add_writer(storage, config, "Gardener", "garden", "roses tulips compost pruning")
        embed_missing(storage, provider)

        report = people_like(storage, names=["Mario Rossi", "Brian Chen"])
        assert report.reference == "Mario Rossi + Brian Chen"
        names = [entry.name for entry in report.people]
        assert names[0] == "Bridge Person"
        assert "Mario Rossi" not in names and "Brian Chen" not in names


def test_similarity_fails_visibly_without_embeddings(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_writer(storage, config, "Jane Author", "jane", "words about things")
        with pytest.raises(IngestError, match="wingman embed"):
            similar_people(storage, name="Jane Author")
        with pytest.raises(IngestError, match="no person named"):
            similar_people(storage, name="Nobody")
        with pytest.raises(IngestError, match="no embeddings"):
            similar_people(storage)


def test_switching_models_reembeds_instead_of_stranding(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_writer(storage, config, "Jane Author", "jane", "kafka streaming pipelines")
        first = embed_missing(storage, HashedEmbeddingProvider(model="hashed-a"))
        assert first.external_embedded == 1 and first.reembedded == 0
        # switching the configured model re-embeds rather than leaving a mixed workspace
        second = embed_missing(storage, HashedEmbeddingProvider(model="hashed-b"))
        assert second.external_embedded == 1 and second.reembedded == 1
        assert storage.embedding_models_in_use() == {("hashed", "hashed-b")}
        # and similarity runs instead of refusing
        similar_people(storage, name="Jane Author")


def test_people_like_requires_two_names(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_writer(storage, config, "Jane Author", "jane", "kafka streaming")
        embed_missing(storage, HashedEmbeddingProvider())
        with pytest.raises(IngestError, match="at least two"):
            people_like(storage, names=["Jane Author"])
        with pytest.raises(IngestError, match="at least two"):
            people_like(storage, names=[])


def test_mismatched_dimensions_fail_with_guidance(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_writer(storage, config, "Jane Author", "jane", "kafka streaming")
        add_writer(storage, config, "Gardener", "garden", "roses tulips")
        documents = storage.list_external_documents()
        # same provider/model but different dimensions (e.g. a partial manual re-embed)
        storage.upsert_embedding(documents[0].doc_id, "external", "hashed", "h", [1.0, 0.0])
        storage.upsert_embedding(documents[1].doc_id, "external", "hashed", "h", [1.0, 0.0, 0.0])
        with pytest.raises(IngestError, match="mismatched dimensions"):
            similar_people(storage, name="Jane Author")


def test_mixed_models_refuse_to_compare(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_writer(storage, config, "Jane Author", "jane", "kafka streaming")
        add_writer(storage, config, "Gardener", "garden", "roses tulips")
        documents = storage.list_external_documents()
        storage.upsert_embedding(documents[0].doc_id, "external", "voyage", "voyage-4", [1.0, 0.0])
        storage.upsert_embedding(
            documents[1].doc_id, "external", "hashed", "hashed-256", [0.0, 1.0]
        )
        with pytest.raises(IngestError, match="different models"):
            similar_people(storage, name="Jane Author")
