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
from wingman.providers.embeddings import (
    BATCH_SIZE,
    MAX_BATCH_CHARS,
    TEXT_CHAR_LIMIT,
    EmbeddingProvider,
)

_logger = get_logger("application.similarity")


class EmbedReport(BaseModel):
    provider: str
    model: str
    corpus_embedded: int
    external_embedded: int
    reembedded: int
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


_DIMENSION_GUIDANCE = (
    "stored embeddings have mismatched dimensions; re-run 'wingman embed' after a "
    "provider or model change so every document is re-embedded consistently."
)


def _dot(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise IngestError(_DIMENSION_GUIDANCE)
    return sum(x * y for x, y in zip(a, b, strict=True))


def _mean(vectors: list[list[float]]) -> list[float]:
    dim = len(vectors[0])
    if any(len(vector) != dim for vector in vectors):
        raise IngestError(_DIMENSION_GUIDANCE)
    total = [0.0] * dim
    for vector in vectors:
        for index, value in enumerate(vector):
            total[index] += value
    return _normalize([value / len(vectors) for value in total])


def embed_missing(storage: Storage, provider: EmbeddingProvider) -> EmbedReport:
    """Embed every corpus and external document that lacks a vector.

    This is the explicit data-egress step: document text goes to the
    configured embeddings provider, once per document. Documents embedded by a
    different provider or model are re-embedded, so switching in models.toml
    can never strand the workspace with incomparable mixed-model vectors.
    """
    pending: list[tuple[str, str, str]] = []  # (doc_id, scope, body)
    already = 0
    reembedded = 0
    skipped_empty = 0

    def matches_active(doc_id: str) -> bool | None:
        """True if embedded by the active model, False if by another, None if missing."""
        embedded = storage.get_embedding(doc_id)
        if embedded is None:
            return None
        return embedded[1] == provider.provider_name and embedded[2] == provider.model

    for document in storage.list_corpus_documents():
        current = matches_active(document.doc_id)
        if current is True:
            already += 1
            continue
        body = storage.get_corpus_body(document.doc_id)
        if not body or not body.strip():
            skipped_empty += 1
            continue
        reembedded += int(current is False)
        pending.append((document.doc_id, "corpus", body))
    for external in storage.list_external_documents():
        current = matches_active(external.doc_id)
        if current is True:
            already += 1
            continue
        body = storage.get_external_body(external.doc_id)
        if not body or not body.strip():
            skipped_empty += 1
            continue
        reembedded += int(current is False)
        pending.append((external.doc_id, "external", body))

    # Pack batches against both provider limits: text count AND total size
    # (Voyage rejects a whole batch over its token cap — a 64-post batch of
    # long essays can exceed it even though each post alone is fine).
    batches: list[list[tuple[str, str, str]]] = []
    open_batch: list[tuple[str, str, str]] = []
    open_chars = 0
    for item in pending:
        length = min(len(item[2]), TEXT_CHAR_LIMIT)
        if open_batch and (len(open_batch) >= BATCH_SIZE or open_chars + length > MAX_BATCH_CHARS):
            batches.append(open_batch)
            open_batch, open_chars = [], 0
        open_batch.append(item)
        open_chars += length
    if open_batch:
        batches.append(open_batch)

    counts = {"corpus": 0, "external": 0}
    for batch in batches:
        vectors = provider.embed([body for _, _, body in batch], input_type="document")
        for (doc_id, scope, _), vector in zip(batch, vectors, strict=True):
            storage.upsert_embedding(
                doc_id, scope, provider.provider_name, provider.model, _normalize(vector)
            )
            counts[scope] += 1
    _logger.info(
        "embed provider=%s model=%s corpus=%d external=%d reembedded=%d already=%d empty=%d",
        provider.provider_name,
        provider.model,
        counts["corpus"],
        counts["external"],
        reembedded,
        already,
        skipped_empty,
    )
    return EmbedReport(
        provider=provider.provider_name,
        model=provider.model,
        corpus_embedded=counts["corpus"],
        external_embedded=counts["external"],
        reembedded=reembedded,
        already_embedded=already,
        skipped_empty=skipped_empty,
    )


class SimilarCompany(BaseModel):
    name: str
    score: float
    people: int
    documents: int


class CompanySimilarityReport(BaseModel):
    reference: str
    companies: list[SimilarCompany] = Field(default_factory=list)


class _CompanySignal(BaseModel):
    display_name: str
    vectors: list[list[float]] = Field(default_factory=list)
    person_ids: set[str] = Field(default_factory=set)
    doc_ids: set[str] = Field(default_factory=set)

    model_config = {"arbitrary_types_allowed": True}


def company_key(name: str) -> str:
    return " ".join(name.lower().split())


def _company_vectors(storage: Storage) -> dict[str, _CompanySignal]:
    """company key -> aggregated writing signal, from two sources (slice i):

    a person's documents count toward the company on their Person record, and
    org-attributed documents (RFC-011 company blogs) count toward that
    organization — deduplicated per document when the two coincide.
    """
    people_by_id = {person.person_id: person for person in storage.list_people()}
    signals: dict[str, _CompanySignal] = {}

    def contribute(company_name: str, doc_id: str, vector: list[float], person_id: str) -> None:
        key = company_key(company_name)
        if not key:
            return
        signal = signals.setdefault(key, _CompanySignal(display_name=company_name.strip()))
        if doc_id in signal.doc_ids:
            return
        signal.doc_ids.add(doc_id)
        signal.vectors.append(vector)
        signal.person_ids.add(person_id)

    for document in storage.list_external_documents():
        embedded = storage.get_embedding(document.doc_id)
        if embedded is None:
            continue
        person = people_by_id.get(document.person_id)
        if person is not None and person.company:
            contribute(person.company, document.doc_id, embedded[0], document.person_id)
        if document.organization:
            contribute(document.organization, document.doc_id, embedded[0], document.person_id)
    return signals


def company_alignment(storage: Storage, name: str) -> float | None:
    """Cosine of a company's aggregate vector against the user's corpus, as enrichment.

    Returns None whenever the comparison is unavailable (no embeddings on
    either side, or mixed models) — callers use this to annotate, never to
    gate, so it degrades silently instead of raising.
    """
    try:
        _require_one_model(storage)
        reference = _corpus_vector(storage)
        if reference is None:
            return None
        signal = _company_vectors(storage).get(company_key(name))
        if signal is None:
            return None
        return _dot(reference, _mean(signal.vectors))
    except IngestError:
        return None


def _rank_companies(
    reference: list[float],
    signals: dict[str, _CompanySignal],
    exclude_keys: set[str],
    limit: int,
) -> list[SimilarCompany]:
    ranked = [
        SimilarCompany(
            name=signal.display_name,
            score=round(_dot(reference, _mean(signal.vectors)), 4),
            people=len(signal.person_ids),
            documents=len(signal.doc_ids),
        )
        for key, signal in signals.items()
        if key not in exclude_keys
    ]
    ranked.sort(key=lambda entry: entry.score, reverse=True)
    return ranked[:limit]


def similar_companies(
    storage: Storage, name: str | None = None, limit: int = 10
) -> CompanySimilarityReport:
    """Rank companies by the writing of their people and blogs — vs one company or vs you."""
    _require_one_model(storage)
    signals = _company_vectors(storage)
    if not signals:
        raise IngestError(
            "no company has embedded writing yet. Watch people with a company set "
            "(or org-attributed feeds), fetch, and run 'wingman embed' first."
        )
    if name is None:
        reference = _corpus_vector(storage)
        if reference is None:
            raise IngestError(
                "your corpus has no embeddings yet. Run 'wingman corpus add' and then "
                "'wingman embed' first."
            )
        return CompanySimilarityReport(
            reference="your corpus",
            companies=_rank_companies(reference, signals, exclude_keys=set(), limit=limit),
        )
    key = company_key(name)
    signal = signals.get(key)
    if signal is None:
        raise IngestError(
            f"no embedded writing is attributable to {name!r}. Companies come from "
            "watched people's company field and org-attributed feeds."
        )
    reference = _mean(signal.vectors)
    return CompanySimilarityReport(
        reference=signal.display_name,
        companies=_rank_companies(reference, signals, exclude_keys={key}, limit=limit),
    )


def companies_like(storage: Storage, names: list[str], limit: int = 10) -> CompanySimilarityReport:
    """'If these companies interest you, look at…': rank companies near their centroid."""
    if len(names) < 2:
        raise IngestError(
            "name at least two companies to blend — for a single company, "
            "use 'wingman company similar <name>'."
        )
    _require_one_model(storage)
    signals = _company_vectors(storage)
    references: list[list[float]] = []
    exclude: set[str] = set()
    for name in names:
        key = company_key(name)
        signal = signals.get(key)
        if signal is None:
            raise IngestError(
                f"no embedded writing is attributable to {name!r}. Companies come from "
                "watched people's company field and org-attributed feeds."
            )
        references.append(_mean(signal.vectors))
        exclude.add(key)
    reference = _mean(references)
    return CompanySimilarityReport(
        reference=" + ".join(names),
        companies=_rank_companies(reference, signals, exclude_keys=exclude, limit=limit),
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


def corpus_alignment(storage: Storage, person_id: str) -> float | None:
    """Cosine of one person's vector against the user's corpus, as enrichment.

    Returns None whenever the comparison is unavailable (no embeddings on
    either side, or mixed models) — callers use this to annotate, never to
    gate, so it degrades silently instead of raising.
    """
    try:
        _require_one_model(storage)
        reference = _corpus_vector(storage)
        if reference is None:
            return None
        entry = _person_vectors(storage).get(person_id)
        if entry is None:
            return None
        return _dot(reference, entry[0])
    except IngestError:
        return None


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
    if len(names) < 2:
        raise IngestError(
            "name at least two people to blend — for a single person, "
            "use 'wingman people similar <name>'."
        )
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
