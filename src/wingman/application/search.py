"""Unified search across everything the workspace knows (RFC-022).

One query, every store: your corpus, watched people's writing, POV stances
and company themes, news snapshots, research links, and outreach briefs —
returned as a single ranked list where every hit says what it is, who it
belongs to, when it happened, and where it came from.

Keyword matching runs everywhere (FTS5 for full-text stores, token match
for structured artifacts); on top of it, a semantic pass embeds the query
and surfaces documents that match by meaning even when they share no words
with the query. Egress, stated plainly: with a remote embeddings provider
configured (voyage), the query text is sent to that provider — the same
trip document text makes during 'wingman embed'. With the local 'hashed'
provider, or no embeddings at all, search stays fully on the machine, and
the semantic pass degrades visibly, never silently.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field

from wingman.application.corpus import find_evidence
from wingman.application.ingest import IngestError
from wingman.application.people import find_people_evidence
from wingman.application.pov import COMPANY_POV_PREFIX, CORPUS_PERSON_ID
from wingman.application.similarity import _dot, _normalize, _require_one_model
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import CorpusSearchError, Storage
from wingman.providers.embeddings import EmbeddingError
from wingman.providers.router import ModelConfigError, get_embedding_provider

_logger = get_logger("application.search")

_SNIPPET_CHARS = 240
# Below this cosine similarity a semantic candidate is noise, not recall.
MIN_SEMANTIC_SIMILARITY = 0.2


class SearchHit(BaseModel):
    kind: str  # corpus | writing | stance | news | research-link | brief
    title: str
    snippet: str
    who: str  # attribution: author, person, or company
    when: str  # ISO date or "undated"
    source: str  # URL or locator
    rank: int  # 1-based rank within its own store


class SearchReport(BaseModel):
    query: str
    hits: list[SearchHit] = Field(default_factory=list)
    searched: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _tokens(query: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", query.lower()) if token]


def _matches(tokens: list[str], haystack: str) -> bool:
    """All query tokens must appear — the same AND semantics FTS5 defaults to."""
    lowered = haystack.lower()
    return all(token in lowered for token in tokens)


def _when(value: datetime | None) -> str:
    return value.date().isoformat() if value else "undated"


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _SNIPPET_CHARS else text[: _SNIPPET_CHARS - 1] + "…"


def _semantic_hits(
    query: str,
    storage: Storage,
    config: Config,
    per_store: int,
    seen_doc_ids: set[str],
) -> tuple[list[SearchHit], str | None]:
    """Meaning-matched documents keyword search missed; (hits, skip-note).

    Skips — never fails — when embeddings are unconfigured, absent, or from
    a different model than the configured provider; the note says why.
    """
    try:
        _require_one_model(storage)
        provider = get_embedding_provider(config)
    except (IngestError, ModelConfigError) as exc:
        return [], f"semantic pass skipped: {exc}"
    models = storage.embedding_models_in_use()
    if not models:
        return [], "semantic pass skipped: no embeddings yet — run 'wingman embed'"
    stored = next(iter(models))
    if stored != (provider.provider_name, provider.model):
        return [], (
            f"semantic pass skipped: stored vectors are {stored[0]}/{stored[1]} but "
            f"models.toml configures {provider.provider_name}/{provider.model}"
        )
    try:
        query_vector = _normalize(provider.embed([query], input_type="query")[0])
    except (EmbeddingError, IndexError) as exc:
        return [], f"semantic pass skipped: {exc}"

    people_by_id = {person.person_id: person for person in storage.list_people()}
    scored: list[tuple[float, str, str, str, str, str]] = []
    for document in storage.list_corpus_documents():
        if document.doc_id in seen_doc_ids:
            continue
        embedded = storage.get_embedding(document.doc_id)
        if embedded is None:
            continue
        score = _dot(query_vector, embedded[0])
        if score >= MIN_SEMANTIC_SIMILARITY:
            scored.append(
                (
                    score,
                    document.doc_id,
                    document.title,
                    "you",
                    _when(document.published_at),
                    document.url or "corpus document",
                )
            )
    for external in storage.list_external_documents():
        if external.doc_id in seen_doc_ids:
            continue
        embedded = storage.get_embedding(external.doc_id)
        if embedded is None:
            continue
        score = _dot(query_vector, embedded[0])
        if score >= MIN_SEMANTIC_SIMILARITY:
            person = people_by_id.get(external.person_id)
            scored.append(
                (
                    score,
                    external.doc_id,
                    external.title,
                    person.name if person else "unknown",
                    _when(external.published_at),
                    external.url or "stored document",
                )
            )
    scored.sort(key=lambda entry: entry[0], reverse=True)
    hits = [
        SearchHit(
            kind="semantic",
            title=title,
            snippet=f"matched by meaning, not keywords (similarity {score:.2f})",
            who=who,
            when=when,
            source=source,
            rank=rank,
        )
        for rank, (score, _doc_id, title, who, when, source) in enumerate(
            scored[:per_store], start=1
        )
    ]
    return hits, None


def search_workspace(query: str, storage: Storage, config: Config, limit: int = 12) -> SearchReport:
    """Search every store and interleave the results by per-store rank."""
    if not query.strip():
        raise IngestError("the search query is empty.")
    tokens = _tokens(query)
    if not tokens:
        raise IngestError("the search query has no searchable words.")
    per_store = max(3, limit)
    report = SearchReport(query=query)
    columns: list[list[SearchHit]] = []

    # Your corpus (FTS5 relevance, real snippets)
    try:
        corpus_hits = find_evidence(query, storage, limit=per_store)
    except CorpusSearchError as exc:
        raise IngestError(f"search failed: {exc}") from exc
    report.searched.append("corpus")
    columns.append(
        [
            SearchHit(
                kind="corpus",
                title=hit.document.title,
                snippet=_clip(hit.snippet),
                who="you",
                when=_when(hit.document.published_at),
                source=hit.source_locator,
                rank=rank,
            )
            for rank, hit in enumerate(corpus_hits, start=1)
        ]
    )

    # Watched people's writing (FTS5)
    writing_hits = find_people_evidence(query, storage, limit=per_store)
    report.searched.append("writing")
    columns.append(
        [
            SearchHit(
                kind="writing",
                title=hit.document.title,
                snippet=_clip(hit.snippet),
                who=hit.person_name,
                when=_when(hit.document.published_at),
                source=hit.document.url or "stored document",
                rank=rank,
            )
            for rank, hit in enumerate(writing_hits, start=1)
        ]
    )

    # Semantic pass: meaning-matched documents the keyword columns missed
    seen_doc_ids = {hit.document.doc_id for hit in corpus_hits} | {
        hit.document.doc_id for hit in writing_hits
    }
    semantic_hits, semantic_note = _semantic_hits(query, storage, config, per_store, seen_doc_ids)
    report.searched.append("semantic")
    if semantic_note:
        report.notes.append(semantic_note)
    columns.append(semantic_hits)

    # POV stances and company themes (token match over statement+quote+topics)
    stance_hits: list[SearchHit] = []
    for card in storage.list_pov_cards():
        if card.person_id == CORPUS_PERSON_ID:
            who = "you (your corpus card)"
        elif card.person_id.startswith(COMPANY_POV_PREFIX):
            who = card.person_name
        else:
            who = card.person_name
        for stance in card.stances:
            haystack = f"{stance.statement} {stance.quote} {stance.doc_title}"
            if _matches(tokens, haystack):
                label = f"[{stance.dimension.value}] " if stance.dimension else ""
                stance_hits.append(
                    SearchHit(
                        kind="stance",
                        title=f"{label}{stance.statement}",
                        snippet=_clip(f'"{stance.quote}"'),
                        who=who,
                        when=card.generated_at.date().isoformat(),
                        source=stance.doc_title,
                        rank=len(stance_hits) + 1,
                    )
                )
    report.searched.append("stances")
    columns.append(stance_hits[:per_store])

    # News snapshots (title match)
    people_by_id = {person.person_id: person for person in storage.list_people()}
    news_hits: list[SearchHit] = []
    for item in reversed(storage.list_news_items()):  # newest first
        if _matches(tokens, item.title):
            person = people_by_id.get(item.person_id)
            news_hits.append(
                SearchHit(
                    kind="news",
                    title=item.title,
                    snippet="",
                    who=person.name if person else "unknown",
                    when=_when(item.published_at),
                    source=item.url,
                    rank=len(news_hits) + 1,
                )
            )
    report.searched.append("news")
    columns.append(news_hits[:per_store])

    # Research snapshots (approved-source links — hiring signals live here)
    research_hits: list[SearchHit] = []
    for snapshot in storage.list_research_snapshots():
        for link in snapshot.links:
            if _matches(tokens, link):
                research_hits.append(
                    SearchHit(
                        kind="research-link",
                        title=link,
                        snippet=f"seen on {snapshot.url}",
                        who=snapshot.company_key,
                        when=snapshot.fetched_at.date().isoformat(),
                        source=snapshot.url,
                        rank=len(research_hits) + 1,
                    )
                )
    report.searched.append("research")
    columns.append(research_hits[:per_store])

    # Outreach briefs (talking points + intro bullets)
    brief_hits: list[SearchHit] = []
    for brief in storage.list_outreach_briefs():
        for point in brief.talking_points:
            haystack = f"{point.point} {point.their_stance} {point.your_quote}"
            if _matches(tokens, haystack):
                brief_hits.append(
                    SearchHit(
                        kind="brief",
                        title=point.point,
                        snippet=_clip(
                            f'their: "{point.their_stance}" / yours: "{point.your_quote}"'
                        ),
                        who=brief.person_name,
                        when=brief.pov_generated_at.date().isoformat(),
                        source=f"outreach brief ({brief.purpose.value})",
                        rank=len(brief_hits) + 1,
                    )
                )
        for bullet in brief.intro_points:
            if _matches(tokens, bullet):
                brief_hits.append(
                    SearchHit(
                        kind="brief",
                        title=_clip(bullet),
                        snippet="intro material",
                        who=brief.person_name,
                        when=brief.pov_generated_at.date().isoformat(),
                        source=f"outreach brief ({brief.purpose.value})",
                        rank=len(brief_hits) + 1,
                    )
                )
    report.searched.append("briefs")
    columns.append(brief_hits[:per_store])

    # Interleave by per-store rank: every store's best answer surfaces before
    # any store's third-best. Deterministic tie-break by column order above.
    interleaved: list[SearchHit] = []
    depth = 0
    while len(interleaved) < limit:
        row = [column[depth] for column in columns if depth < len(column)]
        if not row:
            break
        interleaved.extend(row)
        depth += 1
    report.hits = interleaved[:limit]
    _logger.info("search query=%s hits=%d", query, len(report.hits))
    return report


def render_search_report(report: SearchReport) -> str:
    if not report.hits:
        message = (
            f"Nothing in the workspace matches {report.query!r} "
            f"(searched: {', '.join(report.searched)})."
        )
        return "\n".join([message, *report.notes]) if report.notes else message
    lines = [f"Search: {report.query!r} — {len(report.hits)} hits"]
    for number, hit in enumerate(report.hits, start=1):
        lines.append(f"{number}. [{hit.kind}] {hit.title}")
        detail = f"   {hit.who} · {hit.when} · {hit.source}"
        lines.append(detail)
        if hit.snippet:
            lines.append(f"   {hit.snippet}")
    lines.extend(report.notes)
    return "\n".join(lines)
