"""First-run guidance from inside the conversation (#360).

The walkthrough is written for a person who never touches a terminal, and
lives in a repository they do not have. This is that advice, answering from
the workspace they are actually standing in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wingman.application.completeness import compute_completeness
from wingman.application.setup_guide import render_setup_guide
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


def _guide(config=None) -> str:
    config = config or load_config()
    with Storage(config.db_path) as storage:
        return render_setup_guide(config, compute_completeness(storage, config))


def test_it_leads_with_what_this_workspace_still_needs(workspace: Path) -> None:
    """Not a generic checklist: the outstanding work comes from completeness,
    so there is one opinion about what matters next rather than two that can
    drift apart."""
    guide = _guide()

    assert "What to do next" in guide
    assert "job criteria" in guide.lower()
    assert guide.index("What to do next") < guide.index("Where to look")


def test_a_finished_step_is_named_as_finished(workspace: Path) -> None:
    """Somebody who set their criteria an hour ago and is then told to set
    their criteria stops trusting the rest of the advice."""
    from wingman.application.job_scoring import criteria_path

    config = load_config()
    criteria_path(config).write_text("# Job criteria\n\nAnything.\n", encoding="utf-8")

    guide = _guide(config)

    assert "Already done here" in guide
    assert "job criteria set" in guide
    assert "let's set up my job criteria" not in guide


def test_a_hosted_tenant_is_not_told_to_schedule_what_already_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The most common wrong answer, and it sends them after somebody else's
    job. A tenant-bound config is self-identifying, so this is checkable."""
    monkeypatch.delenv(ENV_DATA_DIR, raising=False)
    data_dir = tmp_path / "tenants" / "trent"
    data_dir.mkdir(parents=True)
    Storage(data_dir / "wingman.db").close()
    config = Tenant(slug="trent", data_dir=data_dir).config()

    guide = _guide(config)

    assert "already happens for you" in guide
    assert "cron" not in guide
    assert "no daemon" not in guide


def test_somebody_running_their_own_copy_is_told_to_schedule_it(workspace: Path) -> None:
    guide = _guide()

    assert "yours to schedule" in guide
    assert "cron or launchd" in guide


def test_it_points_at_the_standing_rhythm_without_inventing_one(workspace: Path) -> None:
    """The cadence is #359's interview to run, not a number this guesses."""
    guide = _guide()

    assert "standing rhythm" in guide
    for guessed in ("every Monday", "8am", "daily at"):
        assert guessed not in guide


def test_it_names_the_three_places_to_look(workspace: Path) -> None:
    guide = _guide()

    for surface in ("Profile", "Progress", "what's my status"):
        assert surface in guide
