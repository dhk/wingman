"""Job interest scoring (RFC-035): criteria doc, judge gates, recall, digest wiring."""

import json
from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.job_scoring import (
    CRITERIA_FILENAME,
    MAX_JUDGED_PER_COMPANY,
    criteria_path,
    judge_posting,
    load_criteria,
    rank_candidates,
    save_criteria,
    score_company_openings,
)
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.embeddings import HashedEmbeddingProvider
from wingman.providers.recorded import RecordedProvider

CRITERIA = (
    "## Hard filters\n- Remote or SF only\n- No crypto\n\n"
    "## Wants\n- ML platform scope\n- Staff level\n"
)
POSTING = (
    "Staff ML Engineer\n\nWe are hiring a Staff ML Engineer to own the training "
    "platform end to end. Fully remote within the US. You will lead a small team "
    "and set technical direction for the feature store and evaluation stack."
)


def _judgment(**overrides: object) -> str:
    base: dict[str, object] = {
        "title": "Staff ML Engineer",
        "score": 84,
        "hard_filter_failed": "",
        "reasons": ["ML platform scope: owns the training platform"],
        "quotes": ["own the training\nplatform end to end"],  # hard-wrapped: fold must match
    }
    base.update(overrides)
    return json.dumps(base)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    return config


def test_criteria_roundtrip_and_empty_refusal(workspace: Config) -> None:
    assert load_criteria(workspace) is None  # absent
    save_criteria(workspace, CRITERIA)
    assert criteria_path(workspace).name == CRITERIA_FILENAME
    assert "No crypto" in (load_criteria(workspace) or "")
    with pytest.raises(IngestError, match="empty"):
        save_criteria(workspace, "   \n")
    criteria_path(workspace).write_text("", encoding="utf-8")
    assert load_criteria(workspace) is None  # empty file counts as absent


def test_judge_verifies_quotes_and_clamps_score() -> None:
    opening = judge_posting(
        POSTING,
        CRITERIA,
        RecordedProvider(_judgment(score=250)),
        url="https://x.example/j/1",
        company="Acme",
    )
    assert opening.score == 100  # clamped
    assert opening.quotes == ["own the training\nplatform end to end"]
    assert not opening.filtered

    invented = judge_posting(
        POSTING, CRITERIA, RecordedProvider(_judgment(quotes=["we pay in dogecoin"]))
    )
    assert invented.quotes == []  # unverifiable quote never presented as evidence
    assert any("unverifiable" in reason for reason in invented.reasons)


def test_judge_hard_filter_and_bad_json() -> None:
    filtered = judge_posting(
        POSTING, CRITERIA, RecordedProvider(_judgment(hard_filter_failed="No crypto", score=0))
    )
    assert filtered.filtered and filtered.hard_filter_failed == "No crypto"
    with pytest.raises(IngestError, match="not valid judgment JSON"):
        judge_posting(POSTING, CRITERIA, RecordedProvider("the vibes are good"))


def test_rank_candidates_orders_by_anchor_similarity() -> None:
    embedder = HashedEmbeddingProvider()
    candidates = [
        ("sales", "Enterprise sales director quota pipeline territory"),
        ("ml", "Staff machine learning platform engineer training evaluation"),
    ]
    ranked = rank_candidates(candidates, ["machine learning platform engineer"], embedder)
    assert ranked[0] == "ml"
    # no anchors -> original order, recall never invents a preference
    assert rank_candidates(candidates, [], embedder) == ["sales", "ml"]
    assert rank_candidates([], ["anything"], embedder) == []


def _page(text: str) -> bytes:
    return f"<html><body><main><p>{text}</p></main></body></html>".encode()


def test_score_company_openings_end_to_end(workspace: Config) -> None:
    save_criteria(workspace, CRITERIA)
    Storage(workspace.db_path).close()
    pages = {
        "https://acme.example/jobs/ml": _page(POSTING.replace("\n", " ")),
        "https://acme.example/jobs/thin": b"<html><body>tiny</body></html>",
    }
    with Storage(workspace.db_path) as storage:
        outcome = score_company_openings(
            "Acme",
            list(pages),
            workspace,
            storage,
            RecordedProvider(_judgment(quotes=["own the training platform end to end"])),
            fetcher=lambda url: pages[url],
        )
    assert [opening.score for opening in outcome.scored] == [84]
    assert outcome.scored[0].url == "https://acme.example/jobs/ml"
    assert any("not scored" in note for note in outcome.notes)  # the thin page, visibly

    with Storage(workspace.db_path) as storage, pytest.raises(IngestError, match="judge against"):
        criteria_path(workspace).unlink()
        score_company_openings("Acme", [], workspace, storage, RecordedProvider("{}"))


def test_over_budget_is_ranked_and_reported(workspace: Config) -> None:
    save_criteria(workspace, CRITERIA)
    Storage(workspace.db_path).close()
    body = POSTING.replace("\n", " ")
    pages = {
        f"https://acme.example/jobs/{index}": _page(f"{body} variant {index}")
        for index in range(MAX_JUDGED_PER_COMPANY + 2)
    }
    with Storage(workspace.db_path) as storage:
        outcome = score_company_openings(
            "Acme",
            list(pages),
            workspace,
            storage,
            RecordedProvider(_judgment(quotes=[])),
            fetcher=lambda url: pages[url],
            embedder=HashedEmbeddingProvider(),
        )
    assert len(outcome.scored) == MAX_JUDGED_PER_COMPANY
    assert any("judge budget" in note for note in outcome.notes)


def test_scored_opening_actions_write_keys_and_evidence(workspace: Config) -> None:
    """The digest wiring: scored openings become per-opening keyed actions."""
    from wingman.application.focus import OvernightTarget, _scored_opening_actions

    save_criteria(workspace, CRITERIA)
    Storage(workspace.db_path).close()
    page = _page(POSTING.replace("\n", " "))
    import wingman.providers.router as router_module

    target = OvernightTarget(name="Acme", kind="company", status="ok")
    actions: list = []
    import wingman.infrastructure.fetch as fetch_module

    with Storage(workspace.db_path) as storage, pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            router_module,
            "get_provider",
            lambda capability, config: RecordedProvider(
                _judgment(quotes=["own the training platform end to end"])
            ),
        )
        patch.setattr(fetch_module, "fetch_url", lambda url: page)
        handled = _scored_opening_actions(
            "Acme", ["https://acme.example/jobs/ml"], workspace, storage, target, actions
        )
    assert handled and len(actions) == 1
    action = actions[0]
    assert "scored 84/100" in action.what
    assert action.key == "opening:acme:https://acme.example/jobs/ml"
    assert any("wingman assess --url" in item for item in action.evidence)
    assert any(item.startswith('"own the training') for item in action.evidence)


def test_missing_criteria_leaves_generic_action(workspace: Config) -> None:
    from wingman.application.focus import OvernightTarget, _scored_opening_actions

    Storage(workspace.db_path).close()
    target = OvernightTarget(name="Acme", kind="company", status="ok")
    actions: list = []
    with Storage(workspace.db_path) as storage:
        handled = _scored_opening_actions(
            "Acme", ["https://acme.example/jobs/ml"], workspace, storage, target, actions
        )
    assert not handled and actions == []
    assert any(CRITERIA_FILENAME in line for line in target.lines)


def test_mcp_job_criteria_roundtrip(workspace: Config) -> None:
    from wingman.mcp_server import job_criteria

    Storage(workspace.db_path).close()
    assert "No job-criteria.md yet" in job_criteria("show")
    assert "Saved job-criteria.md" in job_criteria("save", text=CRITERIA)
    assert "No crypto" in job_criteria("show")
    assert "failed" in job_criteria("save", text="  ")
    assert "unknown action" in job_criteria("nope")
    # the docstring carries the interview protocol (RFC-025/030/031 convention)
    assert "AskUserQuestion" in (job_criteria.__doc__ or "")
    assert "confirms the wording" in (job_criteria.__doc__ or "")
