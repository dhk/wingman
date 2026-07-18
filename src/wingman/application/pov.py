"""POV card building: model proposes stances, deterministic validation disposes.

The one model step in the people pipeline. Documents go to the
synthesize_balanced provider; every proposed stance is then checked in
ordinary code — the doc_id must be one we supplied, and the quote must
appear verbatim in that document's stored text. Anything else is rejected
and reported, never stored (the same fabrication guard as resume ingestion).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from wingman.agents.pov_analyst import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_prompt,
    parse_pov_proposal,
)
from wingman.application.ingest import IngestError
from wingman.domain.person import ExternalDocument
from wingman.domain.pov import PovCard, Stance
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelProvider, ModelRequest

_logger = get_logger("application.pov")

# Newest-first document budget for the prompt: enough for a season of writing
# without blowing the context on prolific authors.
MAX_DOCUMENTS = 20
MAX_CHARS_PER_DOCUMENT = 6_000
# The prompt asks for at most 6 stances; enforce it deterministically too.
MAX_STANCES = 6


class RejectedStance(BaseModel):
    statement: str
    reason: str


class PovReport(BaseModel):
    card: PovCard
    rejected: list[RejectedStance] = Field(default_factory=list)


def _documents_block(documents: list[ExternalDocument], bodies: dict[str, str]) -> str:
    parts = []
    for document in documents:
        body = bodies[document.doc_id][:MAX_CHARS_PER_DOCUMENT]
        when = document.published_at.date().isoformat() if document.published_at else "undated"
        parts.append(f"--- doc_id: {document.doc_id}\ntitle: {document.title} ({when})\n{body}\n")
    return "\n".join(parts)


def build_pov_card(name: str, storage: Storage, provider: ModelProvider) -> PovReport:
    """Build (or rebuild) the POV card for a person from their stored writing."""
    name_key = " ".join(name.lower().split())
    person = storage.find_person_by_name_key(name_key)
    if person is None:
        raise IngestError(f"no person named {name!r}; see 'wingman people list'.")
    documents = storage.list_external_documents(person.person_id)
    if not documents:
        raise IngestError(
            f"{person.name} has no stored writing yet. Fetch their sources first "
            "('wingman people fetch'), then build the card."
        )
    documents.sort(key=lambda d: (d.published_at is not None, d.published_at), reverse=True)
    documents = documents[:MAX_DOCUMENTS]
    bodies = {
        document.doc_id: storage.get_external_body(document.doc_id) or "" for document in documents
    }
    documents = [document for document in documents if bodies[document.doc_id].strip()]
    if not documents:
        raise IngestError(f"{person.name}'s stored documents have no extractable text.")
    by_id = {document.doc_id: document for document in documents}

    prompt = build_prompt(person.name, _documents_block(documents, bodies))
    response = provider.complete(ModelRequest(system=SYSTEM_PROMPT, prompt=prompt))
    proposal = parse_pov_proposal(response.text)

    stances: list[Stance] = []
    rejected: list[RejectedStance] = []
    for proposed in proposal.stances:
        statement = proposed.statement.strip()
        quote = proposed.quote.strip()
        if len(stances) >= MAX_STANCES:
            rejected.append(
                RejectedStance(
                    statement=statement or "(empty)",
                    reason=f"over the {MAX_STANCES}-stance limit",
                )
            )
            continue
        if not statement:
            rejected.append(RejectedStance(statement="(empty)", reason="empty statement"))
            continue
        document = by_id.get(proposed.doc_id)
        if document is None:
            rejected.append(
                RejectedStance(
                    statement=statement,
                    reason=f"doc_id {proposed.doc_id!r} was not among the supplied documents",
                )
            )
            continue
        if not quote or quote not in bodies[document.doc_id]:
            rejected.append(
                RejectedStance(
                    statement=statement,
                    reason=f"quote does not appear verbatim in {document.title!r}",
                )
            )
            continue
        stances.append(
            Stance(
                statement=statement,
                quote=quote,
                doc_id=document.doc_id,
                doc_title=document.title,
                source_record_id=document.source_record_id,
                organization=document.organization,
            )
        )
    if not stances:
        raise IngestError(
            f"no stance survived validation for {person.name} "
            f"({len(rejected)} rejected). The card was not stored; re-run to retry."
        )

    card = PovCard(
        person_id=person.person_id,
        person_name=person.name,
        stances=stances,
        topics=[topic.strip() for topic in proposal.topics if topic.strip()][:8],
        documents_used=len(documents),
        provider=response.provider,
        model=response.model,
        prompt_version=PROMPT_VERSION,
    )
    storage.save_pov_card(card)
    _logger.info(
        "pov_card person=%s documents=%d stances=%d rejected=%d provider=%s model=%s",
        person.name,
        len(documents),
        len(stances),
        len(rejected),
        response.provider,
        response.model,
    )
    return PovReport(card=card, rejected=rejected)


def render_pov_card(card: PovCard) -> str:
    """Deterministic text rendering shared by the CLI and MCP surfaces."""
    lines = [
        f"POV card: {card.person_name}",
        f"(built from {card.documents_used} documents, {card.provider}/{card.model}, "
        f"{card.generated_at.date().isoformat()})",
        "",
        "Stances:",
    ]
    for stance in card.stances:
        via = f" — via {stance.organization}" if stance.organization else ""
        lines.append(f"- {stance.statement}")
        lines.append(f'    "{stance.quote}" ({stance.doc_title}{via})')
    if card.topics:
        lines.append("")
        lines.append("Writes about: " + ", ".join(card.topics))
    return "\n".join(lines)
