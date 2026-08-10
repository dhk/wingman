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
from wingman.application.evidence import fold_whitespace
from wingman.application.ingest import IngestError
from wingman.application.similarity import company_key
from wingman.domain.corpus import CorpusDocument
from wingman.domain.interview import InterviewDocument
from wingman.domain.person import ExternalDocument
from wingman.domain.persona import Persona
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

# Company theme cards live in the same table under a reserved id per company.
COMPANY_POV_PREFIX = "__company__"

# A persona's own POV (docs/COACHING-MODE-DESIGN.md) lives in the same
# table under a reserved id per persona — distinct from CORPUS_PERSON_ID
# (the coach's own), so building one never collides with or overwrites
# the other.
PERSONA_POV_PREFIX = "__persona__"


def persona_card_id(persona_id: str) -> str:
    """pov_cards identity for a persona's own synthesized stance."""
    return f"{PERSONA_POV_PREFIX}{persona_id}"


def company_card_id(key: str) -> str:
    """pov_cards identity for a company's synthesized themes (key is company_key)."""
    return f"{COMPANY_POV_PREFIX}{key}"


PovDocument = ExternalDocument | CorpusDocument | InterviewDocument


def _documents_block(documents: Sequence[PovDocument], bodies: dict[str, str]) -> str:
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


def _newest_mixed(documents: Sequence[PovDocument], limit: int) -> list[PovDocument]:
    """Same newest-first cap as _newest, for build_own_pov's one genuinely
    heterogeneous call: a per-call-constrained generic binds to exactly one
    concrete type, so it can't express a list mixing CorpusDocument and
    InterviewDocument together — this is the plain-union equivalent, used
    only where the mix is real."""

    def key(document: PovDocument) -> tuple[bool, datetime]:
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
    # Verbatim modulo whitespace (RFC-026): stored documents are hard-wrapped.
    folded_bodies = {doc_id: fold_whitespace(body) for doc_id, body in bodies.items()}
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
        if not quote or fold_whitespace(quote) not in folded_bodies[proposed.doc_id]:
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


def build_own_pov(
    storage: Storage, provider: ModelProvider, persona: Persona | None = None
) -> PovReport:
    """Build (or rebuild) the POV card for the user's own corpus, or (with
    persona set) a coached Persona's own stance instead.

    The same machinery as a person's card, pointed at the user's writing
    AND their captured interview reactions/nominations (docs/PROFILE-
    BOOTSTRAP-DESIGN.md) — an assembly of the subject areas where either
    takes a position, each stance backed by a verbatim quote from the
    user's own words. Interview captures make this work even with an
    empty corpus (the bootstrap path for someone without published
    writing); a corpus, when present, simply adds more candidate
    documents to the same pool. Stored under the reserved CORPUS_PERSON_ID.

    With persona set (docs/COACHING-MODE-DESIGN.md), the corpus is
    deliberately EXCLUDED — that's the coach's own writing, and nothing
    crosses into a persona's synthesis unless explicitly captured under
    their name. Only that persona's own scoped interview captures feed
    their stance. Stored under persona_card_id(persona.persona_id),
    distinct from the coach's own card.
    """
    # Local import: application.interview -> application.research ->
    # application.pov (for COMPANY_POV_PREFIX/company_card_id) is already a
    # cycle at module scope; importing here instead of at module level
    # avoids it, same convention this codebase already uses for
    # qa_capture/capture_interview_reaction at CLI/MCP call sites.
    from wingman.application.interview import list_interview_documents

    persona_id = persona.persona_id if persona is not None else None
    corpus_documents = [] if persona is not None else storage.list_corpus_documents()
    interview_documents = list_interview_documents(storage, persona_id=persona_id)
    if not corpus_documents and not interview_documents:
        if persona is not None:
            raise IngestError(
                f"nothing has been captured for {persona.name} yet — 'coach_persona set "
                f"{persona.name!r}' first, then capture with 'wingman interview' or "
                "interview_react."
            )
        raise IngestError(
            "your corpus is empty and you haven't captured any interview reactions yet — "
            "add writing with 'wingman corpus add', or capture one with 'wingman interview'."
        )
    documents: list[PovDocument] = _newest_mixed(
        [*corpus_documents, *interview_documents], MAX_DOCUMENTS
    )
    bodies: dict[str, str] = {
        document.doc_id: storage.get_corpus_body(document.doc_id) or ""
        for document in documents
        if isinstance(document, CorpusDocument)
    }
    bodies.update(
        {
            document.doc_id: document.body
            for document in documents
            if isinstance(document, InterviewDocument)
        }
    )
    documents = [document for document in documents if bodies[document.doc_id].strip()]
    if not documents:
        raise IngestError("your corpus documents and interview captures have no extractable text.")

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
        person_id=persona_card_id(persona.persona_id) if persona is not None else CORPUS_PERSON_ID,
        person_name=persona.name if persona is not None else CORPUS_PERSON_NAME,
        stances=stances,
        topics=[topic.strip() for topic in proposal.topics if topic.strip()][:8],
        documents_used=len(documents),
        provider=response.provider,
        model=response.model,
        prompt_version=PROMPT_VERSION,
    )
    storage.save_pov_card(card)
    _logger.info(
        "own_pov persona_id=%s documents=%d stances=%d rejected=%d provider=%s model=%s",
        persona_id,
        len(documents),
        len(stances),
        len(rejected),
        response.provider,
        response.model,
    )
    return PovReport(card=card, rejected=rejected)


def build_company_pov(name: str, storage: Storage, provider: ModelProvider) -> PovReport:
    """Build (or rebuild) synthesized themes for a company from its document pool.

    The pool is the same one the dossier reads: writing by watched people at
    the company plus org-attributed documents (RFC-016). Author attribution
    rides each document's title so the model knows who is speaking, and every
    proposed theme passes the same verbatim-quote validation as any POV card —
    a company theme is still only as real as the sentence it quotes.
    """
    key = company_key(name)
    if not key:
        raise IngestError("company name is empty — nothing to synthesize.")
    all_people = storage.list_people()
    people_by_id = {person.person_id: person for person in all_people}
    documents: list[ExternalDocument] = []
    authors: dict[str, str] = {}
    for document in storage.list_external_documents():
        person = people_by_id.get(document.person_id)
        via_person = person is not None and company_key(person.company or "") == key
        via_org = company_key(document.organization or "") == key
        if not (via_person or via_org):
            continue
        documents.append(document)
        if via_person and person is not None:
            authors[document.doc_id] = person.name
        else:
            authors[document.doc_id] = document.organization or "organization feed"
    if not documents:
        raise IngestError(
            f"nothing in the workspace is attributable to {name!r}. Companies come from "
            "watched people's company field and org-attributed feeds; fetch some writing first."
        )
    display = next(
        (
            person.company
            for person in all_people
            if person.company and company_key(person.company) == key
        ),
        next((document.organization for document in documents if document.organization), name),
    )
    assert display is not None  # at least one branch above produced a name

    documents = _newest(documents, MAX_DOCUMENTS)
    bodies = {
        document.doc_id: storage.get_external_body(document.doc_id) or "" for document in documents
    }
    documents = [document for document in documents if bodies[document.doc_id].strip()]
    if not documents:
        raise IngestError(f"{display}'s attributable documents have no extractable text.")

    # Attribution rides the title: the model sees who wrote each document, and
    # the attributed title is what gets stored on each surviving stance.
    attributed = [
        document.model_copy(update={"title": f"{document.title} — by {authors[document.doc_id]}"})
        for document in documents
    ]
    prompt = build_prompt(
        f"the people of {display} (a company; write statements about what "
        f"{display}'s people collectively argue, naming authors where it helps)",
        _documents_block(attributed, bodies),
    )
    response = provider.complete(ModelRequest(system=SYSTEM_PROMPT, prompt=prompt))
    proposal = parse_pov_proposal(response.text)
    stances, rejected = _validate_proposal(
        proposal,
        {
            document.doc_id: (document.title, document.source_record_id, document.organization)
            for document in attributed
        },
        bodies,
    )
    if not stances:
        raise IngestError(
            f"no theme survived validation for {display} "
            f"({len(rejected)} rejected). Nothing was stored; re-run to retry."
        )
    card = PovCard(
        person_id=company_card_id(key),
        person_name=f"{display} (company)",
        stances=stances,
        topics=[topic.strip() for topic in proposal.topics if topic.strip()][:8],
        documents_used=len(documents),
        provider=response.provider,
        model=response.model,
        prompt_version=PROMPT_VERSION,
    )
    storage.save_pov_card(card)
    _logger.info(
        "company_pov company=%s documents=%d stances=%d rejected=%d provider=%s model=%s",
        display,
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
        (
            f"(built from {card.documents_used} documents, {card.provider}/{card.model}, "
            f"{card.generated_at.date().isoformat()})"
        ),
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
