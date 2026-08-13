"""The heap (#113): capture-first inbox, heat-ordered.

The sort half lives in test_heap_sort.py; what matters here is that capture
stays unconditional and that the tool still names its own limits honestly.
"""

from pathlib import Path

import pytest

from wingman.application.heap import (
    add_to_heap,
    hot_unsorted_count,
    list_heap,
    remove_from_heap,
    render_heap,
)
from wingman.application.ingest import IngestError
from wingman.domain.heap import HeapHeat
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    return config


def test_add_captures_unconditionally_defaulting_to_warm(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        saved = add_to_heap(["https://example.com/job", "not-even-a-url"], storage)
        assert len(saved) == 2
        assert all(item.heat is HeapHeat.WARM for item in saved)
        items = list_heap(storage)
        assert {item.item for item in items} == {"https://example.com/job", "not-even-a-url"}


def test_add_accepts_heat_and_note(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        saved = add_to_heap(["https://x.example/p"], storage, heat="hot", note="event tomorrow")
        assert saved[0].heat is HeapHeat.HOT
        assert saved[0].note == "event tomorrow"


def test_add_rejects_empty_list_and_unknown_heat(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="required"):
            add_to_heap(["  ", ""], storage)
        with pytest.raises(IngestError, match="unknown heat"):
            add_to_heap(["https://x.example"], storage, heat="scorching")


def test_list_heap_orders_hot_first(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        add_to_heap(["cold-one"], storage, heat="cold")
        add_to_heap(["warm-one"], storage, heat="warm")
        add_to_heap(["hot-one"], storage, heat="hot")
        items = list_heap(storage)
        assert [item.item for item in items] == ["hot-one", "warm-one", "cold-one"]


def test_remove_by_id_prefix(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        saved = add_to_heap(["https://x.example/a"], storage)
        item_id = saved[0].item_id
        removed = remove_from_heap(item_id[:8], storage)
        assert removed.item_id == item_id
        assert list_heap(storage) == []


def test_remove_rejects_empty_and_unknown_id(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="empty"):
            remove_from_heap("  ", storage)
        with pytest.raises(IngestError, match="no heap item"):
            remove_from_heap("deadbeef", storage)


def test_hot_unsorted_count(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        assert hot_unsorted_count(storage) == 0
        add_to_heap(["a"], storage, heat="hot")
        add_to_heap(["b"], storage, heat="hot")
        add_to_heap(["c"], storage, heat="warm")
        assert hot_unsorted_count(storage) == 2
        # removing a hot item drops the count
        hot_items = [item for item in list_heap(storage) if item.heat is HeapHeat.HOT]
        remove_from_heap(hot_items[0].item_id, storage)
        assert hot_unsorted_count(storage) == 1


def test_render_heap_empty_and_populated(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        assert "empty" in render_heap(list_heap(storage))
        add_to_heap(["https://x.example/a"], storage, heat="hot", note="hiring event")
        rendered = render_heap(list_heap(storage))
        assert "[hot]" in rendered and "https://x.example/a" in rendered
        assert "hiring event" in rendered
        assert "1 item(s)" in rendered


def test_mcp_heap_tool_add_show_remove(workspace: Config) -> None:
    from wingman.mcp_server import heap as heap_tool

    Storage(workspace.db_path).close()  # _ready_config requires the db file to exist
    added = heap_tool(action="add", items=["https://x.example/a"], heat="hot")
    assert "Captured 1 item(s)" in added
    shown = heap_tool(action="show")
    assert "https://x.example/a" in shown and "[hot]" in shown
    with Storage(workspace.db_path) as storage:
        item_id = list_heap(storage)[0].item_id
    removed = heap_tool(action="remove", item_id=item_id)
    assert "Removed" in removed
    assert "empty" in heap_tool(action="show")
    assert "unknown action" in heap_tool(action="bogus")
    # capture-first protocol rides the docstring
    assert "unconditional" in (heap_tool.__doc__ or "")
    # 'sort' now exists, and the docstring has to say what it still cannot
    # do — screenshots are recognised, not read (no vision path yet).
    assert "routes nothing" in (heap_tool.__doc__ or "")
    # Screenshots are LISTED by sort and read by the client via heap_read (#392).
    assert "heap_read" in (heap_tool.__doc__ or "")
