"""A copy of an operator message, taken at the moment it was delivered
(issue #382, RFC-070).

RFC-065 kept one scalar per account: the id of the last message shown. That
is enough to decide "have I seen this", and it is nothing at all when the
question is "what did it SAY". The text lives in `/etc/wingman/motd.json`,
which is the operator's to replace, and acknowledgement means *shown*, not
*read* — so somebody who ran `what's my status` while distracted has spent
their one delivery, and once the operator moves on there is no copy of the
instruction anywhere on the box.

This is that copy, and the shape is deliberately the same one RFC-067 chose
for an answer: **the operator's words are frozen into a row in the tenant's
own database at the moment they are consumed** — the question onto the
answer at save time, the message onto this row at acknowledge time. One
principle, two moments, because the moments are genuinely different (a
message is delivered once, a question stands until answered). Nothing
shared becomes writable in either half.

A stored row is what was DELIVERED, never what is current. The shared file
has its own reader (`wingman motd show`), and conflating the two would put
the answer to "what am I being told now" and "what was I told" in one place
where a reader cannot tell which they are looking at.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

# Duplicated from `infrastructure.broadcast.ALL_TENANTS` rather than
# imported: dependencies point inward (AGENTS.md), so domain cannot read
# infrastructure. One word, compared nowhere in this layer — it is a default
# for a field the writer always sets explicitly.
_EVERYBODY = "all"


class DeliveredMessage(BaseModel):
    """One operator message, as this account was shown it.

    `message_id` is the broadcast id — the same opaque string the operator
    set, which is also what makes "the same message delivered twice" a
    thing this store can recognise and refuse to record twice.

    `action`, `why` and `how` are the operator's words, copied verbatim.
    Nothing here re-reads the shared file to render them: a row that had to
    consult `/etc/wingman/motd.json` to be readable would answer nothing on
    the day the operator replaced it, which is the day somebody asks.

    `to` is who the message was addressed to (`"all"`, or this account's own
    slug). Kept because it changes how the same sentence reads months later
    — "everybody was told to re-ingest" and "I was told to re-ingest" are
    different facts, and the shared file will no longer be able to say which
    this was.

    `delivered_at` is when this account was shown it, not when the operator
    wrote it. The file carries no timestamp, and inventing one from the
    file's mtime would be a claim about somebody else's editor.
    """

    message_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    why: str = ""
    how: str = ""
    to: str = _EVERYBODY
    delivered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
