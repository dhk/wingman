"""Digest action triage (RFC-031): verdicts persist and stop the roll-over."""

from pathlib import Path

import pytest

import wingman.application.focus as focus_module
from wingman.application.focus import ActionItem, OvernightTarget, overnight_run
from wingman.application.ingest import IngestError
from wingman.application.triage import (
    active_suppressions,
    filter_actions,
    mute_action,
    render_verdicts,
    snooze_action,
    unmute_action,
)
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def test_verdict_lifecycle(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        mute_action("company-posts:acme", storage)
        until = snooze_action("research:acme:https://acme.example.com/careers", storage, days=3)
        active = active_suppressions(storage)
        assert active["company-posts:acme"] == "muted"
        assert active["research:acme:https://acme.example.com/careers"] == f"snoozed until {until}"
        assert "2 action key(s) suppressed" in render_verdicts(storage)
        assert unmute_action("company-posts:acme", storage)
        assert not unmute_action("company-posts:acme", storage)
        with pytest.raises(IngestError, match="empty"):
            mute_action("  ", storage)
        with pytest.raises(IngestError, match="at least 1 day"):
            snooze_action("k", storage, days=0)


def test_expired_snooze_prunes_itself(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        storage.set_action_verdict("stale-key", "snooze", until="2020-01-01")
        assert active_suppressions(storage) == {}
        assert storage.list_action_verdicts() == []  # pruned on read


def test_filter_actions_applies_verdicts(workspace: Config) -> None:
    actions = [
        ActionItem(what="Read Acme's new posts", why="w", who="Acme", key="company-posts:acme"),
        ActionItem(what="Assess opening", why="w", who="Acme", key="research:acme:https://x/jobs"),
        ActionItem(what="Keyless legacy action", why="w", who="Acme"),
    ]
    with Storage(workspace.db_path) as storage:
        kept, suppressed = filter_actions(actions, storage)
        assert suppressed == 0 and len(kept) == 3
        mute_action("company-posts:acme", storage)
        kept, suppressed = filter_actions(actions, storage)
        assert suppressed == 1
        assert [action.key for action in kept] == ["research:acme:https://x/jobs", ""]


def test_overnight_digest_honors_verdicts(
    workspace: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_deep(
        name: str,
        config: Config,
        storage: Storage,
        actions: list[ActionItem],
        **_kwargs: object,
    ) -> OvernightTarget:
        actions.append(
            ActionItem(
                what=f"Read {name}'s new posts",
                why="2 new posts",
                who=name,
                key="company-posts:acme",
            )
        )
        actions.append(
            ActionItem(
                what="Assess the new opening",
                why="new careers link",
                who=name,
                key="research:acme:https://acme.example.com/jobs/x",
            )
        )
        return OvernightTarget(name=name, kind="company", status="ok")

    monkeypatch.setattr(focus_module, "_company_deep", fake_deep)
    with Storage(workspace.db_path) as storage:
        storage.watchlist_add("overnight", "company", "Acme")
        first = overnight_run(workspace, storage)
        digest = Path(first.digest_path).read_text(encoding="utf-8")
        assert "key: company-posts:acme" in digest  # keys are visible for triage
        mute_action("company-posts:acme", storage)
        second = overnight_run(workspace, storage)
        digest = Path(second.digest_path).read_text(encoding="utf-8")
        assert "Read Acme's new posts" not in digest
        assert "Assess the new opening" in digest  # only the muted key is gone
        assert "1 action(s) suppressed by your triage verdicts" in digest
        assert [action.key for action in second.actions] == [
            "research:acme:https://acme.example.com/jobs/x"
        ]


def test_mcp_action_triage_roundtrip(workspace: Config) -> None:
    from wingman.mcp_server import action_triage

    Storage(workspace.db_path).close()
    assert "Muted company-posts:acme" in action_triage("mute", key="company-posts:acme")
    assert "Snoozed person-posts:jo until" in action_triage("snooze", key="person-posts:jo", days=2)
    listing = action_triage("list")
    assert "company-posts:acme" in listing and "person-posts:jo" in listing
    assert "Unmuted" in action_triage("unmute", key="company-posts:acme")
    assert "No verdict recorded" in action_triage("unmute", key="company-posts:acme")
    assert "failed" in action_triage("mute", key="")
    assert "unknown action" in action_triage("nope")
