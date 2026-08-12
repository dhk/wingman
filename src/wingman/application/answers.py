"""Application answer bank (RFC-030): refined Q+A+context, reusable across applications.

The refinement itself happens in conversation — the connected client
interviews the user (AskUserQuestion where available) until an answer is
concise and theirs — and what lands here is only the settled result:
question as asked, final answer, and the application context it was
refined for. The bank's value is recall: before drafting anything for a
new application, similar already-refined answers are surfaced so good
wording is reused instead of reinvented.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from wingman.application.ingest import IngestError
from wingman.domain.answer import AnswerRecord
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import CorpusSearchError, Storage

_logger = get_logger("application.answers")

_MAX_QUERY_TOKENS = 12


def _similarity_query(text: str) -> str:
    """A crash-proof OR query from a question's words (#68: every token quoted)."""
    seen: dict[str, None] = {}
    for token in re.findall(r"[A-Za-z0-9]+", text.lower()):
        if len(token) > 2:
            seen.setdefault(token)
    return " OR ".join(f'"{token}"' for token in list(seen)[:_MAX_QUERY_TOKENS])


def find_answer(answer_id_prefix: str, storage: Storage) -> AnswerRecord:
    prefix = answer_id_prefix.strip()
    if not prefix:
        raise IngestError("answer id is empty; see 'wingman answers list' for ids.")
    matches = [record for record in storage.list_answers() if record.answer_id.startswith(prefix)]
    if not matches:
        raise IngestError(f"no answer with id {prefix!r}; see 'wingman answers list'.")
    if len(matches) > 1:
        shorts = ", ".join(record.answer_id[:8] for record in matches)
        raise IngestError(f"id {prefix!r} is ambiguous ({shorts}); use more characters.")
    return matches[0]


def save_answer(
    question: str,
    answer: str,
    storage: Storage,
    company: str = "",
    role_title: str = "",
    asked_on: str = "",
    answer_id: str = "",
    source: str = "",
) -> tuple[AnswerRecord, bool]:
    """Store a refined answer; with answer_id, revise that record in place.

    Returns (record, created). Revision keeps created_at and the original
    context unless new context is given — a re-refined answer is still the
    same bank entry, just better.

    'source' records how the answer arrived when it was not settled in
    conversation — an interview form an operator ingested (#287). It reads
    back in the entry's context line, so somebody reviewing the bank can
    tell a form answer from one they refined with the assistant, which are
    different levels of polish and deserve different amounts of trust when
    pasted into an application.
    """
    if not question.strip():
        raise IngestError("the question is empty; nothing to save.")
    if not answer.strip():
        raise IngestError("the answer is empty; refine it first, then save.")
    if answer_id.strip():
        existing = find_answer(answer_id, storage)
        record = existing.model_copy(
            update={
                "question": question.strip(),
                "answer": answer.strip(),
                "company": company.strip() or existing.company,
                "role_title": role_title.strip() or existing.role_title,
                "asked_on": asked_on.strip() or existing.asked_on,
                "source": source.strip() or existing.source,
                "updated_at": datetime.now(UTC),
            }
        )
        storage.save_answer(record)
        _logger.info("answer revised id=%s", record.answer_id)
        return record, False
    record = AnswerRecord(
        question=question.strip(),
        answer=answer.strip(),
        company=company.strip(),
        role_title=role_title.strip(),
        asked_on=asked_on.strip(),
        source=source.strip(),
    )
    storage.save_answer(record)
    _logger.info("answer saved id=%s company=%s", record.answer_id, record.company or "-")
    return record, True


def find_similar(question: str, storage: Storage, limit: int = 5) -> list[tuple[AnswerRecord, str]]:
    """Previously refined answers similar to a question — the recall step."""
    query = _similarity_query(question)
    if not query:
        return []
    try:
        return storage.search_answers(query, limit=limit)
    except CorpusSearchError:
        return []


def remove_answer(answer_id_prefix: str, storage: Storage) -> AnswerRecord:
    record = find_answer(answer_id_prefix, storage)
    storage.delete_answer(record.answer_id)
    return record


def render_answer(record: AnswerRecord) -> str:
    return (
        f"Q: {record.question}\n"
        f"A: {record.answer}\n"
        f"Context: {record.context}  [{record.answer_id[:8]}]"
    )


def render_answer_listing(records: list[AnswerRecord]) -> str:
    if not records:
        return "The answer bank is empty — refined answers accumulate here (RFC-030)."
    lines = []
    for record in records:
        question = record.question if len(record.question) <= 90 else record.question[:89] + "…"
        lines.append(f"{record.answer_id[:8]}  {question}  ({record.context})")
    lines.append(f"{len(records)} answer(s) banked.")
    return "\n".join(lines)
