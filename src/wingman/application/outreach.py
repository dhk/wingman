"""Outreach brief building: compose POV card + own corpus + similarity into a draft.

The second model step in the people pipeline, and pure decision support:
the person's validated stances and excerpts of the user's own writing go to
the synthesize_balanced provider, which proposes talking points and a draft
intro. Deterministic validation keeps a talking point only if its stance is
copied exactly from the stored POV card and its quote appears verbatim in
the user's stored corpus. Wingman never sends anything (RFC-006).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from wingman.agents.outreach_writer import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_prompt,
    parse_outreach_proposal,
)
from wingman.application.ingest import IngestError
from wingman.application.similarity import corpus_alignment
from wingman.domain.corpus import CorpusDocument
from wingman.domain.outreach import OutreachBrief, TalkingPoint
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import CorpusSearchError, Storage
from wingman.providers.base import ModelProvider, ModelRequest

_logger = get_logger("application.outreach")

# Corpus budget for the prompt: enough of the user's own writing to find real
# overlap without drowning the stances.
MAX_CORPUS_DOCUMENTS = 12
MAX_CHARS_PER_DOCUMENT = 4_000
# The prompt asks for at most 5 talking points; enforce it deterministically too.
MAX_TALKING_POINTS = 5
MAX_INTRO_CHARS = 1_500


class RejectedTalkingPoint(BaseModel):
    point: str
    reason: str


class OutreachReport(BaseModel):
    brief: OutreachBrief
    rejected: list[RejectedTalkingPoint] = Field(default_factory=list)


def _fts_query(text: str) -> str:
    """A crash-proof OR query from free text (stance statements, topics)."""
    seen: dict[str, None] = {}
    for token in re.findall(r"[A-Za-z0-9]+", text.lower()):
        if len(token) > 2:
            seen.setdefault(token)
    return " OR ".join(list(seen)[:8])


def _relevant_corpus(storage: Storage, queries: list[str]) -> list[CorpusDocument]:
    """The user's corpus docs most relevant to the card, newest-first fallback."""
    picked: dict[str, CorpusDocument] = {}
    for query_text in queries:
        query = _fts_query(query_text)
        if not query:
            continue
        try:
            hits = storage.search_corpus(query, limit=4)
        except CorpusSearchError:
            continue
        for document, _snippet in hits:
            picked.setdefault(document.doc_id, document)
            if len(picked) >= MAX_CORPUS_DOCUMENTS:
                return list(picked.values())
    if picked:
        return list(picked.values())
    # No FTS overlap at all: fall back to the most recently added documents so
    # the model still sees what the user writes about.
    recent = storage.list_corpus_documents()[-MAX_CORPUS_DOCUMENTS:]
    return list(reversed(recent))


def _corpus_block(documents: list[CorpusDocument], bodies: dict[str, str]) -> str:
    parts = []
    for document in documents:
        body = bodies[document.doc_id][:MAX_CHARS_PER_DOCUMENT]
        parts.append(f"--- corpus_doc_id: {document.doc_id}\ntitle: {document.title}\n{body}\n")
    return "\n".join(parts)


def build_outreach_brief(name: str, storage: Storage, provider: ModelProvider) -> OutreachReport:
    """Build (or rebuild) the outreach brief for a person. Drafts only — never sends."""
    name_key = " ".join(name.lower().split())
    person = storage.find_person_by_name_key(name_key)
    if person is None:
        raise IngestError(f"no person named {name!r}; see 'wingman people list'.")
    card = storage.get_pov_card(person.person_id)
    if card is None:
        raise IngestError(
            f"{person.name} has no POV card yet. Build one first with "
            f"'wingman people pov \"{person.name}\"' — the brief is grounded in its stances."
        )
    if storage.count_corpus_documents() == 0:
        raise IngestError(
            "your corpus is empty, so there is nothing of yours to connect to. "
            "Add your own writing with 'wingman corpus add' first."
        )
    queries = [stance.statement for stance in card.stances] + card.topics
    documents = _relevant_corpus(storage, queries)
    bodies = {
        document.doc_id: storage.get_corpus_body(document.doc_id) or "" for document in documents
    }
    documents = [document for document in documents if bodies[document.doc_id].strip()]
    if not documents:
        raise IngestError("your corpus documents have no extractable text.")
    by_id = {document.doc_id: document for document in documents}
    stance_statements = {stance.statement for stance in card.stances}

    stances_block = "\n".join(
        f'- {stance.statement}\n  their words: "{stance.quote}" ({stance.doc_title})'
        for stance in card.stances
    )
    prompt = build_prompt(person.name, stances_block, _corpus_block(documents, bodies))
    response = provider.complete(ModelRequest(system=SYSTEM_PROMPT, prompt=prompt))
    proposal = parse_outreach_proposal(response.text)

    points: list[TalkingPoint] = []
    rejected: list[RejectedTalkingPoint] = []
    for proposed in proposal.talking_points:
        point = proposed.point.strip()
        stance = proposed.their_stance.strip()
        quote = proposed.your_quote.strip()
        if len(points) >= MAX_TALKING_POINTS:
            rejected.append(
                RejectedTalkingPoint(
                    point=point or "(empty)",
                    reason=f"over the {MAX_TALKING_POINTS}-point limit",
                )
            )
            continue
        if not point:
            rejected.append(RejectedTalkingPoint(point="(empty)", reason="empty point"))
            continue
        if stance not in stance_statements:
            rejected.append(
                RejectedTalkingPoint(
                    point=point,
                    reason="their_stance does not match any stance on the POV card",
                )
            )
            continue
        document = by_id.get(proposed.corpus_doc_id)
        if document is None:
            rejected.append(
                RejectedTalkingPoint(
                    point=point,
                    reason=(
                        f"corpus_doc_id {proposed.corpus_doc_id!r} was not among "
                        "the supplied documents"
                    ),
                )
            )
            continue
        if not quote or quote not in bodies[document.doc_id]:
            rejected.append(
                RejectedTalkingPoint(
                    point=point,
                    reason=f"your_quote does not appear verbatim in {document.title!r}",
                )
            )
            continue
        points.append(
            TalkingPoint(
                point=point,
                their_stance=stance,
                your_quote=quote,
                corpus_doc_id=document.doc_id,
                corpus_doc_title=document.title,
            )
        )
    if not points:
        raise IngestError(
            f"no talking point survived validation for {person.name} "
            f"({len(rejected)} rejected). The brief was not stored; re-run to retry."
        )

    brief = OutreachBrief(
        person_id=person.person_id,
        person_name=person.name,
        talking_points=points,
        draft_intro=proposal.intro.strip()[:MAX_INTRO_CHARS],
        alignment=corpus_alignment(storage, person.person_id),
        corpus_documents_used=len(documents),
        pov_generated_at=card.generated_at,
        provider=response.provider,
        model=response.model,
        prompt_version=PROMPT_VERSION,
    )
    storage.save_outreach_brief(brief)
    _logger.info(
        "outreach_brief person=%s corpus_docs=%d points=%d rejected=%d provider=%s model=%s",
        person.name,
        len(documents),
        len(points),
        len(rejected),
        response.provider,
        response.model,
    )
    return OutreachReport(brief=brief, rejected=rejected)


def render_outreach_brief(brief: OutreachBrief) -> str:
    """Deterministic text rendering shared by the CLI and MCP surfaces."""
    lines = [
        f"Outreach brief: {brief.person_name} (draft — nothing is sent)",
        f"(POV card of {brief.pov_generated_at.date().isoformat()} + "
        f"{brief.corpus_documents_used} of your documents, {brief.provider}/{brief.model}, "
        f"{brief.generated_at.date().isoformat()})",
    ]
    if brief.alignment is not None:
        lines.append(f"Alignment with your corpus: {brief.alignment:.3f}")
    lines.extend(["", "Talking points:"])
    for number, point in enumerate(brief.talking_points, start=1):
        lines.append(f"{number}. {point.point}")
        lines.append(f"   they argue: {point.their_stance}")
        lines.append(f'   you wrote: "{point.your_quote}" ({point.corpus_doc_title})')
    if brief.draft_intro:
        lines.extend(["", "Draft intro (edit before sending — Wingman never sends, RFC-006):"])
        lines.append(brief.draft_intro)
    return "\n".join(lines)
