"""Application answer bank (RFC-030): save, revise, recall across applications."""

from pathlib import Path

import pytest

from wingman.application.answers import (
    find_answer,
    find_similar,
    remove_answer,
    render_answer_listing,
    save_answer,
)
from wingman.application.ingest import IngestError
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def test_save_list_and_context(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        record, created = save_answer(
            "Why do you want to work at Woven?",
            "Because process debt is the problem I keep writing about, and Woven sells the cure.",
            storage,
            company="Woven",
            role_title="Head of Data",
            asked_on="2026-07-20",
        )
        assert created and record.context == "Woven, Head of Data, 2026-07-20"
        listing = render_answer_listing(storage.list_answers())
        assert "Why do you want to work at Woven?" in listing
        assert "1 answer(s) banked." in listing


def test_recall_across_applications(workspace: Config) -> None:
    """The point of the bank: a similar question at a different company recalls it."""
    with Storage(workspace.db_path) as storage:
        save_answer(
            "Why do you want to work at Woven?",
            "Process debt is my research focus.",
            storage,
            company="Woven",
        )
        save_answer(
            "Describe a production incident you handled.",
            "The reconciliation ledger drift at Synctera.",
            storage,
            company="Ramp",
        )
        hits = find_similar("Tell us why you want to work at Cursor?", storage)
        assert hits and hits[0][0].company == "Woven"
        # crash-proof on FTS-hostile questions (#68 lesson)
        assert find_similar('C++ AND "unbalanced', storage) == []
        assert find_similar("", storage) == []


def test_revise_keeps_identity_and_context(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        original, _ = save_answer("Why data?", "First draft.", storage, company="Woven")
        revised, created = save_answer(
            "Why data?", "Tighter second draft.", storage, answer_id=original.answer_id[:8]
        )
        assert not created
        assert revised.answer_id == original.answer_id
        assert revised.company == "Woven"  # context survives a revision
        assert revised.created_at == original.created_at
        assert revised.updated_at >= original.updated_at
        assert len(storage.list_answers()) == 1  # revised, not duplicated
        assert storage.search_answers('"tighter"')[0][0].answer_id == original.answer_id


def test_remove_and_errors(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        record, _ = save_answer("Q one?", "A one.", storage)
        with pytest.raises(IngestError, match="empty"):
            save_answer("  ", "answer", storage)
        with pytest.raises(IngestError, match="empty"):
            save_answer("question", "  ", storage)
        with pytest.raises(IngestError, match="no answer with id"):
            find_answer("zzzz", storage)
        removed = remove_answer(record.answer_id[:8], storage)
        assert removed.answer_id == record.answer_id
        assert storage.list_answers() == []


def test_answers_surface_in_workspace_search(workspace: Config) -> None:
    from wingman.application.search import search_workspace

    with Storage(workspace.db_path) as storage:
        save_answer(
            "Describe a hard migration.",
            "The therapeutic coverage platform cutover.",
            storage,
            company="Infinitus",
        )
        report = search_workspace("therapeutic coverage", storage, workspace, limit=10)
        assert "answers" in report.searched
        hits = [hit for hit in report.hits if hit.kind == "answer"]
        assert hits and hits[0].who == "Infinitus"


def test_mcp_answer_bank_roundtrip(workspace: Config) -> None:
    from wingman.mcp_server import answer_bank

    Storage(workspace.db_path).close()
    saved = answer_bank(
        "save",
        question="Why Wingman?",
        answer="Because evidence beats vibes.",
        company="Anthropic",
        role_title="MTS",
        asked_on="2026-07-20",
    )
    assert "Saved [" in saved and "Anthropic, MTS, 2026-07-20" in saved
    found = answer_bank("find", question="why do you want wingman")
    assert "evidence beats vibes" in found
    listing = answer_bank("list")
    assert "1 answer(s) banked." in listing
    short = listing.split()[0]
    assert "Why Wingman?" in answer_bank("show", answer_id=short)
    assert "Removed" in answer_bank("remove", answer_id=short)
    assert "unknown action" in answer_bank("nope")
    assert "failed" in answer_bank("show", answer_id="zzz")
