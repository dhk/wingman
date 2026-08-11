"""A tenant's answer to the operator's question (RFC-067, issue #224).

The words are the person's own, and they live in that person's own SQLite
database like every other capture. What is unusual is not whose words they
are but WHO ASKED: every other capture here is offered — a nomination, a
resume, a reading of one's own material — and this one is a reply to
somebody else's question, given knowing that somebody else will read it.

That is why an answer is stored here rather than as a `ProfileItem`, and
why this module carries a banner rather than a `ProfileItemKind`. See
RFC-067 for the decision; the short version is that a reply addressed to
the operator must not become evidence in a fit brief, because the question
that shaped it was authored by whoever will read the answer, and a leading
question would then be a way of writing sentences into somebody else's
career record. The isolation is structural, exactly as the commentary
corpus's is (RFC-058): `operator_answers` is its own table with no FTS
index and no profile kind, so code that does not name it cannot read it.

The question is copied onto the answer verbatim at save time. An answer
read back without the question that produced it is a sentence with its
meaning removed, and the operator's file will have moved on to the next
question long before anybody reads this one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

# Printed above every rendered answer, on the tenant's side and the
# operator's alike. On the tenant's side it is a reminder of who can read
# this; on the operator's, a reminder of whose words these are.
OPERATOR_ANSWER_BANNER = (
    "ANSWERS TO THE OPERATOR'S QUESTION — each person's own words, given in "
    "reply to somebody else's question. Never evidence, and never quoted as "
    "a claim they volunteered."
)

# Said before the answer is stored and again wherever answers are listed.
# One constant, not prose repeated at three call sites, for the same reason
# NextAction.attribution is one: a disclosure that is retyped is a
# disclosure that eventually says something slightly different.
OPERATOR_ANSWER_DISCLOSURE = (
    "Whoever runs this machine asked this and can read your answer. It is "
    "stored in your own workspace, in your own words, and nowhere else."
)


class OperatorAnswer(BaseModel):
    """One answer to one operator question, in the person's own words.

    `question_id` is the broadcast id the answer replies to — the same
    opaque string the operator set, which is also what tells this workspace
    the question has been answered and should stop being asked.

    `question` is that question's text, frozen here at save time.

    `answer` is verbatim. Nothing tidies, summarises or re-punctuates it
    between the echo the person confirmed and the row written here; a
    paraphrase stored under somebody's name is the failure BP-06 exists to
    prevent, and it is worse here than anywhere else because a third party
    reads the result.
    """

    answer_id: str = Field(default_factory=lambda: str(uuid4()))
    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
