"""Semantic similarity over people and the user's own writing (RFC-010).

Embedding — the only step that sends text to a provider — happens solely in
embed_missing, invoked by the explicit 'wingman embed' command. Everything
downstream is deterministic arithmetic over stored vectors: a person's vector
is the normalized mean of their documents' vectors, the user's vector is the
normalized mean of the corpus, and similarity is a dot product (providers
return unit-length vectors, so this is cosine similarity).
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from wingman.application.ingest import IngestError
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage
from wingman.providers.embeddings import BATCH_SIZE, EmbeddingProvider

_logger = get_logger("application.similarity")


class EmbedReport(BaseModel):
    provider: str
    model: str
    corpus_embedded: int
    external_embedded: int
    already_embedded: int
    skipped_empty: int


class SimilarPerson(BaseModel):
    name: str
    score: float
    documents: int
    company: str | None = None
    position: str | None = None


class SimilarityReport(BaseModel):
    reference: str
    people: list[SimilarPerson] = Field(default_factory=list)


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _mean(vectors: list[list[float]]) -> list[float]:
    dim = len(vectors[0])
    total = [0.0] * dim
    for vector in vectors:
        for index, value in enumerate(vector):
            total[index] += value
    return _normalize([value / len(vectors) for value in total])


def embed_missing(storage: Storage, provider: EmbeddingProvider) -> EmbedReport:
    """Embed every corpus and external document that lacks a vector.

    This is the explicit data-egress step: document text goes to the
    configured embeddings provider, once per document.
    """
    pending: list[tuple[str, str, str]] = []  # (doc_id, scope, body)
    already = 0
    skipped_empty = 0
    for document in storage.list_corpus_documents():
        if storage.get_embedding(document.doc_id) is not None:
            already += 1
            continue
        body = storage.get_corpus_body(document.doc_id)
        if not body or not body.strip():
            skipped_empty += 1
            continue
        pending.append((document.doc_id, "corpus", body))
    for external in storage.list_external_documents():
        if storage.get_embedding(external.doc_id) is not None:
            already += 1
            continue
        body = storage.get_external_body(external.doc_id)
        if not body or not body.strip():
            skipped_empty += 1
            continue
        pending.append((external.doc_id, "external", body))

    counts = {"corpus": 0, "external": 0}
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        vectors = provider.embed([body for _, _, body in batch], input_type="document")
        for (doc_id, scope, _), vector in zip(batch, vectors, strict=True):
            storage.upsert_embedding(
                doc_id, scope, provider.provider_name, provider.model, _normalize(vector)
            )
            counts[scope] += 1
    _logger.info(
        "embed provider=%s model=%s corpus=%d external=%d already=%d empty=%d",
        provider.provider_name,
        provider.model,
        counts["corpus"],
        counts["external"],
        already,
        skipped_empty,
    )
    return EmbedReport(
        provider=provider.provider_name,
        model=provider.model,
        corpus_embedded=counts["corpus"],
        external_embedded=counts["external"],
        already_embedded=already,
        skipped_empty=skipped_empty,
    )


def _require_one_model(storage: Storage) -> None:
    models = storage.embedding_models_in_use()
    if len(models) > 1:
        pretty = ", ".join(f"{provider}/{model}" for provider, model in sorted(models))
        raise IngestError(
            f"embeddings from different models cannot be compared ({pretty}). "
            "Pick one in models.toml and re-run 'wingman embed' after clearing the others."
        )


def _person_vectors(storage: Storage) -> dict[str, tuple[list[float], int]]:
    """person_id -> (normalized mean vector over their embedded documents, doc count)."""
    grouped: dict[str, list[list[float]]] = {}
    for document in storage.list_external_documents():
        embedded = storage.get_embedding(document.doc_id)
        if embedded is None:
            continue
        grouped.setdefault(document.person_id, []).append(embedded[0])
    return {person_id: (_mean(vectors), len(vectors)) for person_id, vectors in grouped.items()}


def _corpus_vector(storage: Storage) -> list[float] | None:
    vectors = [
        embedded[0]
        for document in storage.list_corpus_documents()
        if (embedded := storage.get_embedding(document.doc_id)) is not None
    ]
    return _mean(vectors) if vectors else None


def _rank(
    reference: list[float],
    storage: Storage,
    exclude_person_ids: set[str],
    limit: int,
) -> list[SimilarPerson]:
    ranked: list[SimilarPerson] = []
    for person_id, (vector, documents) in _person_vectors(storage).items():
        if person_id in exclude_person_ids:
            continue
        person = storage.get_person(person_id)
        if person is None:
            continue
        ranked.append(
            SimilarPerson(
                name=person.name,
                score=round(_dot(reference, vector), 4),
                documents=documents,
                company=person.company,
                position=person.position,
            )
        )
    ranked.sort(key=lambda entry: entry.score, reverse=True)
    return ranked[:limit]


def similar_people(storage: Storage, name: str | None = None, limit: int = 10) -> SimilarityReport:
    """Rank people by similarity to one person — or to the user's own corpus."""
    _require_one_model(storage)
    if name is None:
        reference = _corpus_vector(storage)
        if reference is None:
            raise IngestError(
                "your corpus has no embeddings yet. Run 'wingman corpus add' and then "
                "'wingman embed' first."
            )
        return SimilarityReport(
            reference="your corpus",
            people=_rank(reference, storage, exclude_person_ids=set(), limit=limit),
        )
    name_key = " ".join(name.lower().split())
    person = storage.find_person_by_name_key(name_key)
    if person is None:
        raise IngestError(f"no person named {name!r}; see 'wingman people list'.")
    vectors = _person_vectors(storage)
    if person.person_id not in vectors:
        raise IngestError(
            f"{person.name} has no embedded writing yet. Run 'wingman people fetch' for "
            "them and then 'wingman embed'."
        )
    reference = vectors[person.person_id][0]
    return SimilarityReport(
        reference=person.name,
        people=_rank(reference, storage, exclude_person_ids={person.person_id}, limit=limit),
    )


def people_like(storage: Storage, names: list[str], limit: int = 10) -> SimilarityReport:
    """'If you like A and B, talk to…': rank people near the centroid of the named ones."""
    _require_one_model(storage)
    vectors = _person_vectors(storage)
    references: list[list[float]] = []
    exclude: set[str] = set()
    for name in names:
        name_key = " ".join(name.lower().split())
        person = storage.find_person_by_name_key(name_key)
        if person is None:
            raise IngestError(f"no person named {name!r}; see 'wingman people list'.")
        if person.person_id not in vectors:
            raise IngestError(
                f"{person.name} has no embedded writing yet. Run 'wingman people fetch' "
                "for them and then 'wingman embed'."
            )
        references.append(vectors[person.person_id][0])
        exclude.add(person.person_id)
    reference = _mean(references)
    return SimilarityReport(
        reference=" + ".join(names),
        people=_rank(reference, storage, exclude_person_ids=exclude, limit=limit),
    )
