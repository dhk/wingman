"""What the operator has told this account, read back afterwards (#382,
RFC-070).

The delivery half lives in `infrastructure.broadcast`: a message arrives as
the first entry in `completeness.next_actions` (RFC-065) and is shown once.
This is the other end of it — the copy taken at acknowledge time, read out
of the tenant's own database when somebody asks "what was today's message?"

**Why this does not re-read the shared file.** `/etc/wingman/motd.json`
answers "what is current", which is a different question from "what was I
told", and once the operator replaces it the file cannot answer either one
about yesterday. The shared file already has a reader that says what it
says now — `wingman motd show` — and keeping the two apart is what stops a
reader mistaking somebody else's current instruction for the one they were
given.

**Why the questions are not folded in here.** `qotd(action='list')` shows
answers with the question that produced them, under the banner that says
what an answer is (never evidence, and read by whoever asked). Merging both
into one chronology would either drop that banner or attach it to messages
that are not answers — and a list mixing "what I was told" with "what I
said" invites reading the second as an instruction. Same store shape, two
surfaces, cross-referenced rather than merged.
"""

from __future__ import annotations

from wingman.domain.delivered_message import DeliveredMessage
from wingman.infrastructure.broadcast import ALL_TENANTS
from wingman.infrastructure.storage import Storage

# What a person means by "recent" when they ask what they were told. The
# store is not pruned — one row per distinct message, and an operator who
# broadcasts weekly takes a decade to reach a thousand — so the cap is on
# what gets rendered, not on what is kept.
DEFAULT_HISTORY = 10


def recent_messages(storage: Storage, limit: int = DEFAULT_HISTORY) -> list[DeliveredMessage]:
    """Messages this account was shown, most recent first."""
    return storage.list_delivered_messages(max(1, limit))


def render_messages(messages: list[DeliveredMessage], *, pending: bool = False) -> str:
    """The delivered messages, said to be copies rather than the live file.

    `pending` reports that a message is waiting and has not been shown yet,
    without showing it: this is a history reader, and rendering the pending
    message here would make it delivered by a fifth surface that does not
    acknowledge — the message would then arrive again later, and "shown
    once" would have quietly stopped being true.
    """
    waiting = (
        "\n\nThere is also a message you have not been shown yet — it will lead your next "
        "actions the next time you ask what to do (say: what's my status)."
        if pending
        else ""
    )
    if not messages:
        return (
            "Whoever runs this machine has not sent you a message yet — or none had been "
            "delivered to this workspace before it started keeping copies." + waiting
        )
    blocks: list[str] = []
    for record in messages:
        everybody = record.to.lower() == ALL_TENANTS
        scope = "to everyone on this machine" if everybody else f"to {record.to}"
        # The date is labelled because the operator's default id IS a date
        # (`motd set` stamps today when none is given), and two bare dates
        # side by side read as one fact repeated rather than "shown on the
        # 12th, under the id the operator wrote".
        when = record.delivered_at.date().isoformat()
        lines = [
            f"Delivered {when} · id {record.message_id} · {scope}",
            f"    {record.action}",
        ]
        if record.why:
            lines.append(f"    Why: {record.why}")
        if record.how:
            lines.append(f"    How: {record.how}")
        blocks.append("\n".join(lines))
    header = (
        "MESSAGES FROM WHOEVER RUNS THIS MACHINE — copies kept when each was delivered to "
        "you, most recent first. What the shared file says NOW may be something else "
        "entirely ('wingman motd show')."
    )
    return "\n\n".join([header, *blocks]) + waiting


__all__ = [
    "DEFAULT_HISTORY",
    "recent_messages",
    "render_messages",
]
