"""Summarize the local RFC-023 telemetry journal (issue #223).

Pure aggregation over the calling account's OWN already-recorded event log
(`wingman.infrastructure.telemetry`) — no new capture mechanism, and no
cross-account reads (that's #215, explicitly deferred). Three views over
the same event log:

- session count, via a configurable quiescence-gap boundary (a gap between
  consecutive events at least `gap_minutes` long starts a new session —
  the standard session-analytics convention);
- most-frequent commands/tools, a simple frequency count;
- a "dead end" proxy: the command/tool that was the LAST one invoked in a
  session before one of those long quiet gaps, with a count of how often
  that last call itself ended in a non-"ok" outcome — a session trailing
  off right after a particular action, or right after a recorded failure,
  is the first useful abandonment signal.

The final (most recent) session is never treated as a dead end: no gap has
been observed after it yet, so it may simply still be in progress.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from wingman.infrastructure.config import Config
from wingman.infrastructure.telemetry import iter_events

DEFAULT_GAP_MINUTES = 30.0
DEFAULT_TOP_N = 10


def _parse_ts(ts: str) -> datetime | None:
    """Best-effort ISO-8601 parse; a malformed timestamp is skipped, not fatal."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _is_failure(outcome: str) -> bool:
    return outcome != "ok"


class CommandCount(BaseModel):
    """One (surface, name) pair and how often it appears in the log."""

    surface: str
    name: str
    count: int


class DeadEnd(BaseModel):
    """A (surface, name) pair seen as the last call of a session before a gap."""

    surface: str
    name: str
    count: int
    error_count: int


class TelemetrySummary(BaseModel):
    gap_minutes: float
    total_events: int
    dated_events: int
    session_count: int
    top_commands: list[CommandCount] = Field(default_factory=list)
    dead_ends: list[DeadEnd] = Field(default_factory=list)


def summarize(
    config: Config,
    gap_minutes: float = DEFAULT_GAP_MINUTES,
    top_n: int = DEFAULT_TOP_N,
) -> TelemetrySummary:
    """Summarize this account's own telemetry log: sessions, top commands, dead ends.

    Sessions are delimited by any gap between consecutive events (ordered by
    timestamp) at least `gap_minutes` long. Only events with a parseable
    timestamp participate in session grouping and the dead-end heuristic;
    every event — parseable or not — still counts toward `top_commands` and
    `total_events`.
    """
    if gap_minutes <= 0:
        raise ValueError("gap_minutes must be positive")
    if top_n <= 0:
        raise ValueError("top_n must be positive")

    events = list(iter_events(config))
    command_counter: Counter[tuple[str, str]] = Counter()
    dated: list[tuple[datetime, dict[str, Any]]] = []
    for event in events:
        command_counter[(event["surface"], event["name"])] += 1
        parsed = _parse_ts(event["ts"])
        if parsed is not None:
            dated.append((parsed, event))
    dated.sort(key=lambda pair: pair[0])

    gap = timedelta(minutes=gap_minutes)
    sessions: list[list[tuple[datetime, dict[str, Any]]]] = []
    for parsed, event in dated:
        if sessions and parsed - sessions[-1][-1][0] < gap:
            sessions[-1].append((parsed, event))
        else:
            sessions.append([(parsed, event)])

    dead_end_counter: Counter[tuple[str, str]] = Counter()
    dead_end_errors: Counter[tuple[str, str]] = Counter()
    # Every boundary between sessions is an observed quiescent gap; the
    # trailing session has no gap after it yet, so it's excluded — it may
    # simply still be in progress, not abandoned.
    for session in sessions[:-1]:
        _, last_event = session[-1]
        key = (last_event["surface"], last_event["name"])
        dead_end_counter[key] += 1
        if _is_failure(last_event["outcome"]):
            dead_end_errors[key] += 1

    top_commands = [
        CommandCount(surface=surface, name=name, count=count)
        for (surface, name), count in command_counter.most_common(top_n)
    ]
    dead_ends = [
        DeadEnd(
            surface=surface,
            name=name,
            count=count,
            error_count=dead_end_errors.get((surface, name), 0),
        )
        for (surface, name), count in dead_end_counter.most_common(top_n)
    ]

    return TelemetrySummary(
        gap_minutes=gap_minutes,
        total_events=len(events),
        dated_events=len(dated),
        session_count=len(sessions),
        top_commands=top_commands,
        dead_ends=dead_ends,
    )


def render_summary(summary: TelemetrySummary) -> str:
    """The one text rendering shared by the CLI and MCP surfaces (dual-surface parity)."""
    lines = [
        f"Sessions: {summary.session_count}  "
        f"(gap boundary: {summary.gap_minutes:g}m; {summary.total_events} events, "
        f"{summary.dated_events} with a usable timestamp)",
    ]
    lines.append("")
    lines.append("Most frequent:")
    if not summary.top_commands:
        lines.append("  (no events recorded)")
    else:
        for entry in summary.top_commands:
            lines.append(f"  {entry.count:>5}  [{entry.surface}] {entry.name}")
    lines.append("")
    lines.append("Dead ends (last call in a session before a long gap):")
    if not summary.dead_ends:
        lines.append("  (not enough sessions yet to tell)")
    else:
        for dead_end in summary.dead_ends:
            error_note = f", {dead_end.error_count} after a failure" if dead_end.error_count else ""
            lines.append(f"  {dead_end.count:>5}  [{dead_end.surface}] {dead_end.name}{error_note}")
    return "\n".join(lines)
