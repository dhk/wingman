"""The question of the day: asked by the operator, answered in the tenant's
own workspace (RFC-067, issue #224's second half).

The MOTD half (RFC-065) let whoever runs a shared box tell everybody
something. This is the other direction — ask everybody, or one person,
something — and it is deliberately NOT a messaging system. Three rules
carry it:

**The answer is never written outside the workspace that wrote it.** It is
stored in that tenant's own SQLite database like every other capture, and
travels only where the rest of that workspace travels (a backup its owner
takes). Precise wording on purpose: "never leaves" would be a stronger
promise than the code makes, and the same sentence is shown to the person
before they answer. There is no
group-writable directory, no shared write, nothing added to
`/etc/wingman/` beyond one more read-only question file. The operator
reads answers back with the access they already have — the same access
that reads the registry and runs `tenant urls` — so the fan-in is one
operator reading N workspaces rather than N accounts writing to one place,
and RFC-048's share-nothing posture is left intact rather than needing an
answer.

**An answer is not evidence.** It lives in its own table, unreachable from
POV cards, briefs, fit assessment, `evidence` and workspace `search`, for
the reason set out in `domain.operator_answer`: the words are the person's
own, but the question was authored by the person who will read the answer.
Somebody who wants an answer to count as career evidence says it to
wingman as an ordinary capture, which is an act they choose rather than
one a question performed on them.

**The person is told, before they answer, who can read it.** Twice: in the
attribution line the next-actions list is obliged to render, and again in
the echo the assistant must show before saving (BP-06). Consent that
arrives after the words are stored is not consent.

A question repeats until it is answered, unlike a message, which is shown
once. The answer IS the acknowledgement — no marker file, no fourth
delivery call site to forget — and an unanswered question behaving like
every other outstanding next action is both simpler and the right
direction to fail: a question nobody saw is worse than a question asked
twice.
"""

from __future__ import annotations

from pathlib import Path

from wingman.application.ingest import IngestError
from wingman.domain.operator_answer import (
    OPERATOR_ANSWER_BANNER,
    OperatorAnswer,
)
from wingman.infrastructure.broadcast import (
    OperatorQuestion,
    is_addressed_to,
    read_operator_question,
)
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage

_logger = get_logger("application.qotd")


def pending_question(
    config: Config, storage: Storage, path: Path | None = None
) -> OperatorQuestion | None:
    """The question this account has been asked and not yet answered.

    None when there is no question, when it is addressed to somebody else,
    when this workspace has already answered it, or when the shared file is
    absent/unreadable/malformed — `infrastructure.broadcast` collapses that
    last group to the same answer on purpose, because this sits on the path
    of every account's `completeness`.
    """
    question = read_operator_question(path)
    if question is None:
        return None
    if not is_addressed_to(question.to, config):
        return None
    if question.id in storage.answered_question_ids():
        return None
    return question


def save_operator_answer(
    answer: str,
    config: Config,
    storage: Storage,
    path: Path | None = None,
) -> OperatorAnswer:
    """Store this account's answer to the current question, verbatim.

    The question's id and text are read from the shared file at save time
    rather than passed in, so an answer can never be filed against a
    question nobody asked, and is never stored without the words that
    prompted it.

    Deliberately answers the CURRENT question even when this workspace has
    already answered it — a second answer supersedes nothing and deletes
    nothing; both are kept, in the order they were given, because an edited
    answer and a changed mind are different things and only the person who
    wrote them can say which this is.
    """
    body = answer.strip()
    if not body:
        raise IngestError("the answer is empty — nothing was saved.")
    question = read_operator_question(path)
    if question is None:
        raise IngestError(
            "there is no question to answer right now — nobody has asked one, or the "
            "shared question file could not be read ('wingman qotd show' says which)."
        )
    if not is_addressed_to(question.to, config):
        raise IngestError(
            "the current question is addressed to somebody else on this machine, so "
            "there is nothing here for you to answer."
        )
    record = OperatorAnswer(
        question_id=question.id,
        question=question.question,
        answer=body,
    )
    storage.add_operator_answer(record)
    _logger.info(
        "operator question answered question_id=%s answer_id=%s chars=%d",
        record.question_id,
        record.answer_id,
        len(record.answer),
    )
    return record


def list_operator_answers(storage: Storage) -> list[OperatorAnswer]:
    """Every answer this workspace has given, oldest first."""
    return storage.list_operator_answers()


def render_question(question: OperatorQuestion) -> str:
    """The question as the person asked it, with who can read the answer."""
    from wingman.application.completeness import OPERATOR_QUESTION_ATTRIBUTION

    lines = [
        "A question from whoever runs this machine:",
        "",
        f"  {question.question}",
    ]
    if question.why:
        lines.extend(["", f"  Why they are asking: {question.why}"])
    lines.extend(
        [
            "",
            OPERATOR_QUESTION_ATTRIBUTION,
            "",
            "Answer in your own words — say: my answer to the question of the day is …",
        ]
    )
    return "\n".join(lines)


def render_answers(answers: list[OperatorAnswer], *, who: str = "") -> str:
    """A list of answers, always under the banner that says what they are."""
    label = f" — {who}" if who else ""
    if not answers:
        return f"No answers to the operator's question yet{label}."
    blocks: list[str] = []
    for record in answers:
        blocks.append(
            "\n".join(
                [
                    (
                        f"[{record.answer_id[:8]}] {record.created_at.date().isoformat()}"
                        f" · question {record.question_id}"
                    ),
                    f"    Q: {record.question}",
                    *(f"    A: {line}" for line in record.answer.splitlines()),
                ]
            )
        )
    return "\n\n".join([f"{OPERATOR_ANSWER_BANNER}{label}", *blocks])


__all__ = [
    "list_operator_answers",
    "pending_question",
    "render_answers",
    "render_question",
    "save_operator_answer",
]
