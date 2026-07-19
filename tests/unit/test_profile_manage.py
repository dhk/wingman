"""Career-profile management (RFC-027): list, rm, resolve, clear."""

import json
from pathlib import Path

import pytest

from wingman.application.ingest import IngestError, ingest_resume
from wingman.application.profile_manage import (
    clear_profile,
    find_item,
    remove_item,
    render_profile_listing,
    resolve_item,
)
from wingman.domain.profile import ItemStatus
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.recorded import RecordedProvider

RESUME = "# Jo\n\n- Shipped the search rewrite.\n\nSkills: BigQuery daily.\n"


def _response(bigquery_detail: str) -> str:
    return json.dumps(
        {
            "items": [
                {
                    "kind": "achievement",
                    "name": "Search rewrite",
                    "detail": "Shipped the search rewrite.",
                    "classification": "fact",
                    "confidence": 0.9,
                    "quotes": ["Shipped the search rewrite."],
                },
                {
                    "kind": "skill",
                    "name": "BigQuery",
                    "detail": bigquery_detail,
                    "classification": "fact",
                    "confidence": 0.9,
                    "quotes": ["Skills: BigQuery daily."],
                },
            ]
        }
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def _ingest_twice(config: Config, storage: Storage, tmp_path: Path) -> None:
    """Two ingests whose BigQuery detail differs -> one active + one conflict."""
    resume = tmp_path / "resume.md"
    resume.write_text(RESUME, encoding="utf-8")
    ingest_resume(resume, config, storage, RecordedProvider(_response("")))
    ingest_resume(resume, config, storage, RecordedProvider(_response("Used daily.")))


def test_reingest_conflict_shape(workspace: Config, tmp_path: Path) -> None:
    """The situation that motivated RFC-027: re-ingest piles up conflict rows."""
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        items = storage.list_profile_items()
        bigquery = [item for item in items if item.name == "BigQuery"]
        assert len(bigquery) == 2
        assert {item.status for item in bigquery} == {ItemStatus.ACTIVE, ItemStatus.CONFLICT}
        listing = render_profile_listing(items)
        assert "Conflicts (resolve with" in listing
        assert "1 in conflict" in listing


def test_find_item_by_prefix(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        target = storage.list_profile_items()[0]
        assert find_item(target.item_id[:8], storage).item_id == target.item_id
        with pytest.raises(IngestError, match="no profile item"):
            find_item("zzzzzzzz", storage)
        with pytest.raises(IngestError, match="empty"):
            find_item("  ", storage)
        # ambiguity: two items sharing a crafted prefix
        seed = storage.list_profile_items()[0]
        for suffix in ("1", "2"):
            storage.add_profile_item(seed.model_copy(update={"item_id": f"shared-{suffix}"}))
        with pytest.raises(IngestError, match="ambiguous"):
            find_item("shared-", storage)


def test_rm_deletes_and_rerenders(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        conflict = next(
            item for item in storage.list_profile_items() if item.status is ItemStatus.CONFLICT
        )
        removed = remove_item(conflict.item_id[:8], workspace, storage)
        assert removed.item_id == conflict.item_id
        assert storage.get_profile_item(conflict.item_id) is None
    career_md = (workspace.reports_dir / "career.md").read_text(encoding="utf-8")
    assert "Conflicts (need your resolution)" not in career_md


def test_resolve_keeps_challenger_and_drops_rivals(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        challenger = next(
            item for item in storage.list_profile_items() if item.status is ItemStatus.CONFLICT
        )
        winner, rivals = resolve_item(challenger.item_id[:8], workspace, storage)
        assert winner.item_id == challenger.item_id
        assert winner.status is ItemStatus.ACTIVE and winner.conflicts_with is None
        assert len(rivals) == 1
        bigquery = [item for item in storage.list_profile_items() if item.name == "BigQuery"]
        assert [item.item_id for item in bigquery] == [winner.item_id]
    career_md = (workspace.reports_dir / "career.md").read_text(encoding="utf-8")
    assert "Used daily." in career_md  # the challenger's detail is now the active one
    assert "Conflicts (need your resolution)" not in career_md


def test_resolve_keeps_active_and_drops_conflicts(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        active = next(
            item
            for item in storage.list_profile_items()
            if item.name == "BigQuery" and item.status is ItemStatus.ACTIVE
        )
        winner, rivals = resolve_item(active.item_id[:8], workspace, storage)
        assert winner.item_id == active.item_id and len(rivals) == 1
        assert storage.count_profile_items() == 2  # search rewrite + BigQuery


def test_clear_empties_and_reingest_rebuilds(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        removed = clear_profile(workspace, storage)
        assert removed == 3 and storage.count_profile_items() == 0
        career_md = (workspace.reports_dir / "career.md").read_text(encoding="utf-8")
        assert "_None yet._" in career_md
        # the same source re-ingests cleanly: source record reused, items fresh
        resume = tmp_path / "resume.md"
        report = ingest_resume(resume, workspace, storage, RecordedProvider(_response("")))
        assert report.source_reused is True
        assert report.accepted == 2 and report.conflicts == 0


def test_mcp_profile_manage_roundtrip(workspace: Config, tmp_path: Path) -> None:
    from wingman.mcp_server import profile_manage

    with Storage(workspace.db_path) as storage:
        _ingest_twice(workspace, storage, tmp_path)
        challenger = next(
            item for item in storage.list_profile_items() if item.status is ItemStatus.CONFLICT
        )
    listing = profile_manage("list")
    assert "BigQuery" in listing and "Conflicts" in listing
    assert "Kept skill 'BigQuery'" in profile_manage("resolve", challenger.item_id[:8])
    assert "Removed 2 profile items" in profile_manage("clear")
    assert "unknown action" in profile_manage("nope")
    assert "failed" in profile_manage("rm", "zzzzzzzz")
