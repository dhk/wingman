"""How to set this up, answered from inside the conversation (issue #360).

Somebody handed two links has no idea what to do next. The answer exists —
`docs/WALKTHROUGH-HOSTED.md` is written for exactly this person — but it is
a file in a repository they do not have, and that document's whole premise
is that they never touch a terminal. So the instructions live everywhere
except where they are standing.

This collects nothing and changes nothing. It reads the workspace and
returns guidance, the same shape `perspectives_start` uses one step later
in the journey.

Two things keep it from being a recital of the walkthrough:

- It reports what is TRUE HERE. "Add your CV" is noise to somebody with
  seven roles already ingested, and a step somebody has plainly finished
  reads as a machine not paying attention. The unfinished work comes from
  `completeness.next_actions`, so this never grows a second, drifting
  opinion about what matters next.
- It knows which deployment it is in. A tenant on the shared process has
  an overnight run happening for them already; telling them to schedule
  one is the single most common wrong answer, and it wastes their time on
  something that is somebody else's job.
"""

from __future__ import annotations

from wingman.application.completeness import CompletenessReport, next_actions
from wingman.infrastructure.config import Config
from wingman.infrastructure.tenants import is_tenant_config


def _overnight_lines(config: Config) -> list[str]:
    """What is already running, versus what they must arrange themselves."""
    if is_tenant_config(config):
        return [
            (
                "**The overnight run already happens for you.** Somebody else operates "
                "this instance; a refresh runs on their schedule and writes you a dated "
                "digest. There is nothing for you to install, schedule, or keep alive."
            ),
            "  Ask for it with: show me today's digest, then help me triage it",
        ]
    return [
        (
            "**The overnight run is yours to schedule.** `wingman overnight` "
            "deep-refreshes everything you follow and writes a dated digest. Nothing "
            "runs it for you — wingman has no daemon, deliberately."
        ),
        "  Schedule it with cron or launchd (docs/INSTALL.md), then: show me today's digest",
    ]


def render_setup_guide(config: Config, report: CompletenessReport) -> str:
    """First-run guidance, shaped by what this workspace already has."""
    done = _finished_steps(report)
    # The heading follows the workspace. "Getting set up" is right for an
    # empty one and wrong for an established one — and nobody with a
    # populated workspace asks how to set up, so a first-run title is how
    # this tool stops being called by the people it still has answers for
    # (#365). The content already adapted; the label did not.
    lines = [
        "Getting set up with wingman" if not done else "Where you are with wingman",
        "",
        (
            "Wingman helps you find a small number of genuinely good roles and be the "
            "obvious candidate for them — rather than helping you apply to more things. "
            "Everything it tells you traces back to something you or somebody else "
            "actually wrote, and it never sends anything on your behalf."
        ),
        "",
    ]

    todo = next_actions(report)
    if todo:
        lines.extend(["**What to do next**, hardest-working first:", ""])
        for number, action in enumerate(todo, start=1):
            lines.append(f"{number}. {action.title} — {action.why}")
            lines.append(f"   → {action.how}")
        lines.append("")
    else:
        lines.extend(["**Nothing outstanding** — every section has something in it. ", ""])

    if done:
        finished = ", ".join(done)
        lines.extend([f"Already done here, so skip anything that says otherwise: {finished}.", ""])
    if report.job_criteria.exists:
        # Done is not done forever. Criteria age — the digest already raises
        # a criteria-review action once the document is a month old — and
        # presenting them as permanently settled compounds, because every
        # opening scored afterwards is judged against a document nobody
        # revisited (#365).
        lines.extend(
            [
                (
                    "Your job criteria can be revisited whenever they stop fitting — "
                    "say: review my job criteria. They age, and every opening is scored "
                    "against them until you do."
                ),
                "",
            ]
        )

    lines.extend(_overnight_lines(config))
    lines.extend(
        [
            "",
            "**Where to look**",
            "  Your page — uploads at the top, everything written for you underneath.",
            "  Profile — every claim with the evidence behind it, contested ones first.",
            "  Progress — how far through each section you are, and what is missing.",
            "",
            "**When you are lost**, say: what's my status",
            "  It leads with what to do next rather than a wall of counts.",
            "",
            "**What changed lately**, say: what's new in wingman",
            "",
            (
                "**A standing rhythm** — a daily or weekly briefing in your own client "
                "— is worth setting up once the above is in place. Ask for it and "
                "wingman will walk you through the choices and hand you the prompt."
            ),
        ]
    )
    return "\n".join(lines)


def _finished_steps(report: CompletenessReport) -> list[str]:
    """Steps this workspace has plainly completed.

    Named so the guidance can say "skip that": a person who uploaded a CV an
    hour ago and is then told to upload a CV stops trusting the rest of it.
    """
    done: list[str] = []
    if report.career.roles:
        done.append(f"{report.career.roles} roles from your own documents")
    if report.job_criteria.exists:
        done.append("job criteria set, so openings are scored")
    if report.values.profile_built:
        done.append("a values profile built")
    if report.people:
        done.append(f"{len(report.people)} people tracked")
    return done


__all__ = ["render_setup_guide"]
