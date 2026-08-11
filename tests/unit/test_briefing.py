"""The standing briefing (#359): interview first, then emit a prompt.

Wingman cannot install a scheduled task — the client owns its scheduler.
What it owns is deciding what the briefing says, and handing over text that
produces the same briefing every time it fires.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wingman.application.briefing import (
    interview_packet,
    render_prompt,
    render_schedule,
    validate,
)
from wingman.application.ingest import IngestError
from wingman.domain.briefing import BriefingSchedule
from wingman.infrastructure.config import ENV_DATA_DIR, load_config
from wingman.infrastructure.storage import Storage
from wingman.infrastructure.tenants import Tenant


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True, exist_ok=True)
    Storage(config.db_path).close()
    return tmp_path


def _schedule(**over: object) -> BriefingSchedule:
    base = {
        "cadence": "weekdays",
        "time_of_day": "08:00",
        "timezone": "America/Los_Angeles",
        "items": ["digest", "next"],
    }
    base.update(over)
    return BriefingSchedule(**base)  # type: ignore[arg-type]


def test_the_interview_asks_the_timezone_rather_than_assuming(workspace: Path) -> None:
    """The briefing fires in the person's timezone; the overnight runs on the
    host's. One scheduled too early quietly reports yesterday."""
    packet = interview_packet()

    assert "timezone" in packet.lower()
    assert "reports yesterday" in packet


def test_the_prompt_names_tools_not_intentions(workspace: Path) -> None:
    """'Give me an update' produces a guess, and a different guess each time.
    Naming the calls is what makes a standing briefing worth standing."""
    prompt = render_prompt(_schedule(), load_config())

    assert "digest" in prompt
    assert "action_triage" in prompt
    assert "completeness" in prompt
    assert "every weekday at 08:00 America/Los_Angeles" in prompt


def test_a_hosted_tenant_is_not_told_to_run_the_overnight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Theirs runs on the host's schedule. Telling them to start it wastes a
    model call on somebody else's job — and the warning they DO need is the
    opposite one, that firing too early reads yesterday's digest."""
    monkeypatch.delenv(ENV_DATA_DIR, raising=False)
    data_dir = tmp_path / "tenants" / "trent"
    data_dir.mkdir(parents=True)
    Storage(data_dir / "wingman.db").close()

    prompt = render_prompt(_schedule(), Tenant(slug="trent", data_dir=data_dir).config())

    assert "Run overnight" not in prompt
    assert "host's schedule" in prompt
    assert "yesterday's digest" in prompt


def test_somebody_running_their_own_copy_starts_it_themselves(workspace: Path) -> None:
    prompt = render_prompt(_schedule(), load_config())

    assert "Run overnight if it has not run today" in prompt
    assert "self-sufficient" in prompt


def test_it_says_wingman_cannot_install_the_schedule(workspace: Path) -> None:
    assert "cannot install this" in render_prompt(_schedule(), load_config())


def test_an_empty_or_unknown_selection_is_refused(workspace: Path) -> None:
    with pytest.raises(IngestError, match="at least one"):
        validate(_schedule(items=[]))
    with pytest.raises(IngestError, match="unknown briefing item"):
        validate(_schedule(items=["horoscope"]))
    with pytest.raises(IngestError, match="cadence must be"):
        validate(_schedule(cadence="fortnightly"))


def test_the_answers_survive_so_the_interview_is_not_repeated(workspace: Path) -> None:
    """Changing the time should not mean answering five questions again, and
    two people in one workspace should get the same briefing."""
    config = load_config()
    with Storage(config.db_path) as storage:
        storage.save_briefing_schedule(_schedule(cadence="weekly", day_of_week="Monday"))

    with Storage(config.db_path) as later:
        saved = later.get_briefing_schedule()

    assert saved is not None
    assert "every Monday at 08:00" in saved.when()
    assert "every Monday" in render_schedule(saved)
