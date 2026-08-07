"""wingman telemetry summary (issue #223): session boundaries, most-frequent
commands, and the dead-end heuristic — all pure aggregation over synthesized
telemetry event fixtures written straight into the local journal.
"""

from pathlib import Path

import pytest

from wingman.application.telemetry_summary import render_summary, summarize
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.infrastructure.telemetry import record_event, set_enabled


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "ws"
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(data_dir))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    Storage(config.db_path).close()
    set_enabled(config, True)
    return data_dir


def test_empty_log_summarizes_to_nothing(workspace: Path) -> None:
    config = load_config()
    summary = summarize(config)
    assert summary.total_events == 0
    assert summary.session_count == 0
    assert summary.top_commands == []
    assert summary.dead_ends == []
    assert "no events recorded" in render_summary(summary).lower()


def test_single_session_has_no_dead_end(workspace: Path) -> None:
    """One session with no observed gap after it yet isn't an abandonment —
    it may simply still be in progress.
    """
    config = load_config()
    record_event(config, "cli", "status", {}, ts="2026-08-01T09:00:00+00:00")
    record_event(config, "cli", "search", {}, ts="2026-08-01T09:05:00+00:00")
    record_event(config, "cli", "assess", {}, ts="2026-08-01T09:10:00+00:00")

    summary = summarize(config, gap_minutes=30)
    assert summary.total_events == 3
    assert summary.dated_events == 3
    assert summary.session_count == 1
    assert summary.dead_ends == []


def test_quiescence_gap_splits_sessions(workspace: Path) -> None:
    config = load_config()
    # Session 1: three events a few minutes apart, ending on "search".
    record_event(config, "cli", "status", {}, ts="2026-08-01T09:00:00+00:00")
    record_event(config, "cli", "assess", {}, ts="2026-08-01T09:05:00+00:00")
    record_event(config, "cli", "search", {}, ts="2026-08-01T09:10:00+00:00")
    # 40 minutes of quiet > the 30-minute default boundary: new session.
    record_event(config, "mcp", "people_pov", {}, ts="2026-08-01T09:50:00+00:00")
    record_event(config, "mcp", "company_pov", {}, ts="2026-08-01T09:53:00+00:00")
    # Another 45-minute gap starts a third (trailing) session.
    record_event(config, "cli", "status", {}, ts="2026-08-01T10:38:00+00:00")

    summary = summarize(config, gap_minutes=30)
    assert summary.session_count == 3
    # Trailing session excluded: exactly the two real, observed gaps count.
    dead_end_names = {(d.surface, d.name): d.count for d in summary.dead_ends}
    assert dead_end_names == {
        ("cli", "search"): 1,
        ("mcp", "company_pov"): 1,
    }


def test_gap_boundary_is_a_strict_threshold(workspace: Path) -> None:
    """Exactly `gap_minutes` of quiet still starts a new session (>=, not >)."""
    config = load_config()
    record_event(config, "cli", "a", {}, ts="2026-08-01T09:00:00+00:00")
    record_event(config, "cli", "b", {}, ts="2026-08-01T09:30:00+00:00")  # exactly 30m later
    record_event(config, "cli", "c", {}, ts="2026-08-01T09:45:00+00:00")

    summary = summarize(config, gap_minutes=30)
    assert summary.session_count == 2
    assert [d.name for d in summary.dead_ends] == ["a"]


def test_dead_end_tracks_failure_outcomes(workspace: Path) -> None:
    config = load_config()
    record_event(config, "cli", "ingest", {}, outcome="exit-1", ts="2026-08-01T09:00:00+00:00")
    # Long gap: "ingest" trailed off right after failing.
    record_event(config, "cli", "status", {}, ts="2026-08-01T11:00:00+00:00")
    # Another session, same command, but this time it succeeded before the gap.
    record_event(config, "cli", "search", {}, outcome="ok", ts="2026-08-01T13:00:00+00:00")
    record_event(config, "cli", "ingest", {}, outcome="ok", ts="2026-08-01T13:05:00+00:00")
    record_event(config, "cli", "status", {}, ts="2026-08-01T15:00:00+00:00")

    summary = summarize(config, gap_minutes=30)
    by_name = {d.name: d for d in summary.dead_ends}
    assert by_name["ingest"].count == 2
    assert by_name["ingest"].error_count == 1
    assert "after a failure" in render_summary(summary)


def test_most_frequent_commands_ranked_and_capped(workspace: Path) -> None:
    config = load_config()
    ts = "2026-08-01T09:00:00+00:00"
    for _ in range(5):
        record_event(config, "cli", "search", {}, ts=ts)
    for _ in range(3):
        record_event(config, "mcp", "people_pov", {}, ts=ts)
    for _ in range(1):
        record_event(config, "cli", "status", {}, ts=ts)

    summary = summarize(config, top_n=2)
    assert [(c.surface, c.name, c.count) for c in summary.top_commands] == [
        ("cli", "search", 5),
        ("mcp", "people_pov", 3),
    ]


def test_events_without_a_parseable_timestamp_still_count_toward_totals(
    workspace: Path,
) -> None:
    config = load_config()
    record_event(config, "cli", "search", {}, ts="not-a-timestamp")
    record_event(config, "cli", "status", {}, ts="2026-08-01T09:00:00+00:00")

    summary = summarize(config)
    assert summary.total_events == 2
    assert summary.dated_events == 1
    assert summary.session_count == 1


def test_invalid_gap_or_top_n_rejected(workspace: Path) -> None:
    config = load_config()
    with pytest.raises(ValueError):
        summarize(config, gap_minutes=0)
    with pytest.raises(ValueError):
        summarize(config, top_n=0)


def test_render_summary_smoke(workspace: Path) -> None:
    config = load_config()
    record_event(config, "cli", "status", {}, ts="2026-08-01T09:00:00+00:00")
    summary = summarize(config)
    text = render_summary(summary)
    assert "Sessions: 1" in text
    assert "status" in text
