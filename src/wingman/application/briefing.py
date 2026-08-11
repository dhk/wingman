"""The standing-briefing interview, and the prompt it produces (issue #359).

Wingman cannot install a scheduled task — the client owns its scheduler.
This returns the interview to run and, once answered, the prompt to
schedule. See `domain.briefing`.
"""

from __future__ import annotations

from wingman.application.ingest import IngestError
from wingman.domain.briefing import BRIEFING_ITEMS, CADENCES, BriefingSchedule
from wingman.infrastructure.config import Config
from wingman.infrastructure.tenants import is_tenant_config


def interview_packet() -> str:
    """The questions to put to the person, before anything is written.

    Returned rather than asked, because the tool cannot ask — the calling
    assistant runs AskUserQuestion, the same division `job_criteria`'s
    review packet already uses.
    """
    items = "\n".join(f"  - {key}: {label}" for key, label in BRIEFING_ITEMS.items())
    return (
        "Standing briefing — ask these before saving anything, one card, "
        "AskUserQuestion where the client supports it:\n"
        f"1. How often? One of: {', '.join(CADENCES)}. If weekly, which day.\n"
        "2. What time of day?\n"
        "3. Which timezone? Ask rather than assume — the briefing fires in "
        "THEIR timezone while the overnight run happens on the host's, and a "
        "briefing scheduled too early reports yesterday.\n"
        "4. What should it include? Any of:\n"
        f"{items}\n\n"
        "Then call action='save' with the answers. Save nothing they have not "
        "confirmed."
    )


def validate(schedule: BriefingSchedule) -> BriefingSchedule:
    if schedule.cadence not in CADENCES:
        raise IngestError(f"cadence must be one of {', '.join(CADENCES)}; got {schedule.cadence!r}")
    if not schedule.time_of_day.strip():
        raise IngestError("a time of day is required — the schedule has to fire at some point.")
    unknown = [item for item in schedule.items if item not in BRIEFING_ITEMS]
    if unknown:
        raise IngestError(
            f"unknown briefing item(s): {', '.join(unknown)}. "
            f"Choose from: {', '.join(BRIEFING_ITEMS)}"
        )
    if not schedule.items:
        raise IngestError("pick at least one thing for the briefing to cover.")
    return schedule


def render_prompt(schedule: BriefingSchedule, config: Config) -> str:
    """The text to schedule, naming tools rather than intentions.

    "Give me an update" produces a guess, and a different guess each time.
    Naming the calls produces the same briefing every morning, which is the
    entire value of a standing one.
    """
    hosted = is_tenant_config(config)
    lines = [f"Schedule this {schedule.when()}:", "", '"""']
    lines.append("Give me my wingman briefing.")
    lines.append("")
    if "digest" in schedule.items:
        if hosted:
            lines.append(
                "- Call digest for today's overnight results, then walk me through "
                "triaging the action list with action_triage."
            )
        else:
            lines.append(
                "- Run overnight if it has not run today, then call digest and walk me "
                "through triaging the action list with action_triage."
            )
    if "next" in schedule.items:
        lines.append(
            "- Call completeness and lead with its Things to do list. Answer from that "
            "list; do not invent a step that is not in it."
        )
    if "tracking" in schedule.items:
        lines.append(
            "- Call watchlist and people_news for anything new on the people and "
            "companies I follow."
        )
    if "changelog" in schedule.items:
        lines.append("- Call changelog for what has changed in wingman since yesterday.")
    if "system" in schedule.items:
        lines.append(
            "- Call artifacts with action='stale' and status, and tell me only what "
            "actually needs my attention."
        )
    lines.extend(
        [
            "",
            "Keep it short. If nothing needs me, say so in one line rather than filling space.",
            '"""',
            "",
        ]
    )
    if not hosted:
        lines.append(
            "Note: you run your own overnight, so this prompt starts it if it has not "
            "run today. That makes the briefing slower but self-sufficient."
        )
    else:
        lines.append(
            "Note: your overnight run happens on the host's schedule, not yours. If "
            "this briefing fires before it finishes you will be reading yesterday's "
            "digest — move it later if that happens."
        )
    lines.append(
        "Wingman cannot install this. Ask your client to create the scheduled task, "
        "or paste it into its scheduler yourself."
    )
    return "\n".join(lines)


def render_schedule(schedule: BriefingSchedule | None) -> str:
    if schedule is None:
        return (
            "No standing briefing saved. Ask for one and wingman will walk you through "
            "the choices, then hand you the prompt to schedule."
        )
    covers = ", ".join(BRIEFING_ITEMS[item] for item in schedule.items)
    return f"Standing briefing: {schedule.when()}.\nCovers: {covers}."


__all__ = ["interview_packet", "render_prompt", "render_schedule", "validate"]
