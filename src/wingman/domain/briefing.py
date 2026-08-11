"""A standing briefing: what to ask for, how often, and when (issue #359).

Wingman cannot create a scheduled task. The client owns its scheduler, and
MCP runs one way. What wingman can do is hand over the exact prompt to
schedule — the same move `connector_urls` makes with a paste-ready
`claude mcp add` line rather than describing one.

Storing the answers matters as much as emitting the prompt: without them,
changing the time means redoing the interview, and two people in one
workspace get two different briefings.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

#: How often the briefing runs. Deliberately three, not a cron expression:
#: this is a choice somebody makes about their week, and a syntax they have
#: to learn is a syntax they will get wrong.
CADENCES = ("daily", "weekdays", "weekly")

#: What a briefing can contain. Each maps to a tool the assistant will
#: actually call, so the emitted prompt names tools rather than intentions
#: — "give me an update" produces a guess, and a different guess each time.
BRIEFING_ITEMS: dict[str, str] = {
    "digest": "today's digest, and help triaging it",
    "next": "what to do next — the Things to do list",
    "changelog": "what changed in wingman since last time",
    "tracking": "updates on the people and companies being tracked",
    "system": "anything the system needs flagging — stale profiles, ageing criteria",
}


class BriefingSchedule(BaseModel):
    """The answers, kept so the prompt can be re-emitted without re-asking."""

    cadence: str
    time_of_day: str
    timezone: str = ""
    day_of_week: str = ""
    items: list[str] = Field(default_factory=list)
    saved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def when(self) -> str:
        """The cadence in words, for the prompt's own first line."""
        zone = f" {self.timezone}" if self.timezone else ""
        if self.cadence == "weekly":
            day = self.day_of_week or "Monday"
            return f"every {day} at {self.time_of_day}{zone}"
        if self.cadence == "weekdays":
            return f"every weekday at {self.time_of_day}{zone}"
        return f"every day at {self.time_of_day}{zone}"


__all__ = ["BRIEFING_ITEMS", "CADENCES", "BriefingSchedule"]
