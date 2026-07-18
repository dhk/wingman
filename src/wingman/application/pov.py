"""POV card building: model proposes stances, deterministic validation disposes.

The one model step in the people pipeline. Documents go to the
synthesize_balanced provider; every proposed stance is then checked in
ordinary code — the doc_id must be one we supplied, and the quote must
appear verbatim in that document's stored text. Anything else is rejected
and reported, never stored (the same fabrication guard as resume ingestion).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from wingman.agents.pov_analyst import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_prompt,
    parse_pov_proposal,
)
from wingman.application.ingest import IngestError
from wingman.domain.corpus import CorpusDocument
from wingman.domain.person import ExternalDocument
from wingman.domain.pov import PovCard, PovProposal, Stance, StanceDimension
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


# The identity key for the user's own corpus card in the pov_cards table.
CORPUS_PERSON_ID = "__corpus__"
CORPUS_PERSON_NAME = "Your corpus"


def _documents_block(
    documents: Sequence[ExternalDocument | CorpusDocument], bodies: dict[str, str]
) -> str:
    parts = []
    for document in documents:
        body = bodies[document.doc_id][:MAX_CHARS_PER_DOCUMENT]
        when = document.published_at.date().isoformat() if document.published_at else "undated"
        parts.append(f"--- doc_id: {document.doc_id}\ntitle: {document.title} ({when})\n{body}\n")
    return "\n".join(parts)


def _newest[DocT: (ExternalDocument, CorpusDocument)](
    documents: list[DocT], limit: int
) -> list[DocT]:
    """Newest-first cap; naive timestamps treated as UTC so mixed dates sort."""

    def key(document: DocT) -> tuple[bool, datetime]:
        when = document.published_at
        if when is None:
            return (False, datetime.min.replace(tzinfo=UTC))
        return (True, when if when.tzinfo else when.replace(tzinfo=UTC))

    return sorted(documents, key=key, reverse=True)[:limit]


def _validate_proposal(
    proposal: PovProposal,
    docs: dict[str, tuple[str, str, str | None]],
    bodies: dict[str, str],
) -> tuple[list[Stance], list[RejectedStance]]:
    """Model proposes, this disposes: doc must be supplied, quote verbatim.

    docs maps doc_id -> (title, source_record_id, organization).
    """
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
        entry = docs.get(proposed.doc_id)
        if entry is None:
            rejected.append(
                RejectedStance(
                    statement=statement,
                    reason=f"doc_id {proposed.doc_id!r} was not among the supplied documents",
                )
            )
            continue
        title, source_record_id, organization = entry
        if not quote or quote not in bodies[proposed.doc_id]:
            rejected.append(
                RejectedStance(
                    statement=statement,
                    reason=f"quote does not appear verbatim in {title!r}",
                )
            )
            continue
        # An invalid dimension never sinks a good stance — it is stored
        # uncategorized rather than guessed.
        try:
            dimension: StanceDimension | None = StanceDimension(proposed.dimension.strip().lower())
        except ValueError:
            dimension = None
        stances.append(
            Stance(
                statement=statement,
                quote=quote,
                doc_id=proposed.doc_id,
                doc_title=title,
                source_record_id=source_record_id,
                organization=organization,
                dimension=dimension,
            )
        )
    return stances, rejected


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
            f"('wingman people fetch \"{person.name}\"'), then build the card."
        )
    documents = _newest(documents, MAX_DOCUMENTS)
    bodies = {
        document.doc_id: storage.get_external_body(document.doc_id) or "" for document in documents
    }
    documents = [document for document in documents if bodies[document.doc_id].strip()]
    if not documents:
        raise IngestError(f"{person.name}'s stored documents have no extractable text.")

    prompt = build_prompt(person.name, _documents_block(documents, bodies))
    response = provider.complete(ModelRequest(system=SYSTEM_PROMPT, prompt=prompt))
    proposal = parse_pov_proposal(response.text)
    stances, rejected = _validate_proposal(
        proposal,
        {
            document.doc_id: (document.title, document.source_record_id, document.organization)
            for document in documents
        },
        bodies,
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


def build_own_pov(storage: Storage, provider: ModelProvider) -> PovReport:
    """Build (or rebuild) the POV card for the user's own corpus.

    The same machinery as a person's card, pointed at the user's writing:
    an assembly of the subject areas where the corpus takes a position,
    each stance backed by a verbatim quote from the user's own documents.
    Stored under the reserved CORPUS_PERSON_ID.
    """
    documents = storage.list_corpus_documents()
    if not documents:
        raise IngestError(
            "your corpus is empty — nothing to take a position from. "
            "Add your writing with 'wingman corpus add' first."
        )
    documents = _newest(documents, MAX_DOCUMENTS)
    bodies = {
        document.doc_id: storage.get_corpus_body(document.doc_id) or "" for document in documents
    }
    documents = [document for document in documents if bodies[document.doc_id].strip()]
    if not documents:
        raise IngestError("your corpus documents have no extractable text.")

    prompt = build_prompt(
        "the author of these documents (write statements as 'The author ...')",
        _documents_block(documents, bodies),
    )
    response = provider.complete(ModelRequest(system=SYSTEM_PROMPT, prompt=prompt))
    proposal = parse_pov_proposal(response.text)
    stances, rejected = _validate_proposal(
        proposal,
        {
            document.doc_id: (document.title, document.source_record_id, None)
            for document in documents
        },
        bodies,
    )
    if not stances:
        raise IngestError(
            f"no stance survived validation for your corpus ({len(rejected)} rejected). "
            "The card was not stored; re-run to retry."
        )
    card = PovCard(
        person_id=CORPUS_PERSON_ID,
        person_name=CORPUS_PERSON_NAME,
        stances=stances,
        topics=[topic.strip() for topic in proposal.topics if topic.strip()][:8],
        documents_used=len(documents),
        provider=response.provider,
        model=response.model,
        prompt_version=PROMPT_VERSION,
    )
    storage.save_pov_card(card)
    _logger.info(
        "own_pov documents=%d stances=%d rejected=%d provider=%s model=%s",
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
        label = f"[{stance.dimension.value}] " if stance.dimension else ""
        lines.append(f"- {label}{stance.statement}")
        lines.append(f'    "{stance.quote}" ({stance.doc_title}{via})')
    if card.topics:
        lines.append("")
        lines.append("Writes about: " + ", ".join(card.topics))
    return "\n".join(lines)
