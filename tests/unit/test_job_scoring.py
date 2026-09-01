"""Job interest scoring (RFC-035): criteria doc, judge gates, recall, digest wiring."""

import json
from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.job_scoring import (
    CRITERIA_FILENAME,
    MAX_FETCHED_PER_COMPANY,
    MAX_JUDGED_PER_COMPANY,
    _posting_text,
    criteria_path,
    criteria_sections,
    judge_posting,
    load_criteria,
    rank_candidates,
    save_criteria,
    score_company_openings,
    score_full_board,
)
from wingman.application.research import add_company_source, research_company
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


def test_criteria_sections_splits_by_heading_with_headingless_fallback() -> None:
    assert criteria_sections(CRITERIA) == [
        ("Hard filters", "- Remote or SF only\n- No crypto"),
        ("Wants", "- ML platform scope\n- Staff level"),
    ]
    # no '## ' headings at all: the whole doc is one section under the
    # file's own name, so hand-written prose stays reachable (#489)
    prose = "Location is deliberately not a filter: remote-first or hybrid."
    assert criteria_sections(prose) == [(CRITERIA_FILENAME, prose)]
    assert criteria_sections("   \n\n  ") == []  # blank doc: no sections


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


def test_ashby_posting_routed_through_public_job_board_api() -> None:
    """#486: an Ashby posting page is a JS shell, so the plain-GET path must
    never be tried — the fetcher below has no entry for the job URL itself,
    only for Ashby's public job-board API, and text still comes back."""
    job_url = "https://jobs.ashbyhq.com/notion/1fc309c8-da20-4ff2-84c7-8b863ece2b0a"
    api_url = "https://api.ashbyhq.com/posting-api/job-board/notion"
    board = {
        "jobs": [
            {
                "id": "1fc309c8-da20-4ff2-84c7-8b863ece2b0a",
                "descriptionPlain": POSTING * 3,  # comfortably over _MIN_POSTING_CHARS
            },
            {"id": "some-other-job", "descriptionPlain": "irrelevant"},
        ]
    }

    def fetch(url: str) -> bytes:
        assert url == api_url  # never falls through to the JS page itself
        return json.dumps(board).encode()

    assert _posting_text(job_url, fetch) == (POSTING * 3).strip()


def test_ashby_lookup_miss_falls_back_to_plain_get_with_a_specific_error() -> None:
    """When the API has no matching job (removed posting, org not public,
    API unreachable), scoring falls back to the ordinary plain GET — and,
    since that's the JS shell, the failure names Ashby specifically rather
    than the generic 'login wall' catch-all."""
    job_url = "https://jobs.ashbyhq.com/notion/does-not-exist"

    def fetch(url: str) -> bytes:
        if url == "https://api.ashbyhq.com/posting-api/job-board/notion":
            return json.dumps({"jobs": []}).encode()
        assert url == job_url
        return b"<html><body>Loading...</body></html>"

    with pytest.raises(IngestError, match="public job-board API lookup also failed"):
        _posting_text(job_url, fetch)


def test_non_ashby_thin_page_keeps_generic_js_rendered_hint() -> None:
    """A non-Ashby JS-only page still gets a named, honest hint rather than
    a bare 'login wall' verdict — it just can't be Ashby-specific."""
    with pytest.raises(IngestError, match="JavaScript-rendered job board"):
        _posting_text("https://example.com/careers/1", lambda url: b"<html>tiny</html>")


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
    # #481: the 2 postings the judge budget cut off are named as pending —
    # a caller must be able to carry them forward, not treat them as done.
    assert outcome.pending == list(pages)[MAX_JUDGED_PER_COMPANY:]


def test_pending_names_exactly_the_budget_skipped_links(workspace: Config) -> None:
    """#481: a link only counts as 'pending' when the budget cut it off
    before it got any outcome. A fetch failure or a judge failure DID get a
    real attempt (and is reported in notes), so it must NOT come back as
    pending — retrying an unfixably-broken link forever would be its own
    kind of silent-loss-shaped bug."""
    save_criteria(workspace, CRITERIA)
    Storage(workspace.db_path).close()
    ok_url = "https://acme.example/jobs/ok"
    fetch_fail_url = "https://acme.example/jobs/thin"
    beyond_fetch_budget_url = "https://acme.example/jobs/beyond"
    pages = {
        ok_url: _page(POSTING.replace("\n", " ")),
        fetch_fail_url: b"<html><body>tiny</body></html>",  # too short: fetch "fails"
    }
    links = [ok_url, fetch_fail_url, beyond_fetch_budget_url]
    with Storage(workspace.db_path) as storage, pytest.MonkeyPatch.context() as patch:
        patch.setattr("wingman.application.job_scoring.MAX_FETCHED_PER_COMPANY", 2)
        outcome = score_company_openings(
            "Acme",
            links,
            workspace,
            storage,
            RecordedProvider(_judgment(quotes=["own the training platform end to end"])),
            fetcher=lambda url: pages[url],
        )
    assert [opening.url for opening in outcome.scored] == [ok_url]
    assert any("not scored" in note for note in outcome.notes)  # the fetch failure, visibly
    assert outcome.pending == [beyond_fetch_budget_url]


def test_full_board_sweep_scores_links_never_flagged_new(workspace: Config) -> None:
    """#488: a posting present since the very first snapshot -- and so never
    once surfaced by research_company's new-links diff -- is still picked up
    by the full-board sweep, because the sweep reads the snapshot's FULL
    current link set rather than the diffed 'new' subset."""
    save_criteria(workspace, CRITERIA)
    Storage(workspace.db_path).close()
    job_url = "https://acme.example/jobs/ml"
    careers_url = "https://acme.example/careers"
    careers_page = (
        f'<html><body><main><a href="{job_url}">ML role</a></main></body></html>'
    ).encode()
    pages = {careers_url: careers_page, job_url: _page(POSTING.replace("\n", " "))}
    with Storage(workspace.db_path) as storage:
        add_company_source("Acme", careers_url, storage)
        # Baseline fetch: job_url is present from the very first snapshot,
        # so research_company's diff never once calls it "new".
        first = research_company("Acme", workspace, storage, fetcher=lambda url: pages[url])
        assert first.results[0].new_links == []
        second = research_company("Acme", workspace, storage, fetcher=lambda url: pages[url])
        assert second.results[0].new_links == []  # unchanged page, still never "new"

        outcome = score_full_board(
            "Acme",
            workspace,
            storage,
            RecordedProvider(_judgment(quotes=["own the training platform end to end"])),
            fetcher=lambda url: pages[url],
        )
    assert [opening.url for opening in outcome.scored] == [job_url]
    assert outcome.scored[0].score == 84


def test_full_board_sweep_bypasses_overnight_budgets(workspace: Config) -> None:
    """#488: the sweep must judge every jobish link, not the overnight
    MAX_FETCHED_PER_COMPANY/MAX_JUDGED_PER_COMPANY-capped subset -- proven
    here with more links than either budget allows."""
    save_criteria(workspace, CRITERIA)
    Storage(workspace.db_path).close()
    total = MAX_FETCHED_PER_COMPANY + MAX_JUDGED_PER_COMPANY  # over BOTH budgets
    job_urls = [f"https://acme.example/jobs/{index}" for index in range(total)]
    careers_url = "https://acme.example/careers"
    links_html = "".join(f'<a href="{url}">role {i}</a>' for i, url in enumerate(job_urls))
    careers_page = f"<html><body><main>{links_html}</main></body></html>".encode()
    body = POSTING.replace("\n", " ")
    pages: dict[str, bytes] = {careers_url: careers_page}
    pages.update({url: _page(f"{body} variant {i}") for i, url in enumerate(job_urls)})
    with Storage(workspace.db_path) as storage:
        add_company_source("Acme", careers_url, storage)
        outcome = score_full_board(
            "Acme",
            workspace,
            storage,
            RecordedProvider(_judgment(quotes=[])),
            fetcher=lambda url: pages[url],
        )
    assert len(outcome.scored) == total  # every link judged, no overnight budget applied
    assert not any("budget" in note for note in outcome.notes)


def test_full_board_sweep_reports_its_own_cap_when_it_fires(workspace: Config) -> None:
    """The sweep's own SWEEP_LINK_CAP still bounds one invocation -- distinct
    from (and visibly reported like) the overnight budgets, never silent."""
    import wingman.application.job_scoring as job_scoring_module

    save_criteria(workspace, CRITERIA)
    Storage(workspace.db_path).close()
    cap = 3
    total = cap + 2
    job_urls = [f"https://acme.example/jobs/{index}" for index in range(total)]
    careers_url = "https://acme.example/careers"
    links_html = "".join(f'<a href="{url}">role {i}</a>' for i, url in enumerate(job_urls))
    careers_page = f"<html><body><main>{links_html}</main></body></html>".encode()
    body = POSTING.replace("\n", " ")
    pages: dict[str, bytes] = {careers_url: careers_page}
    pages.update({url: _page(f"{body} variant {i}") for i, url in enumerate(job_urls)})
    with Storage(workspace.db_path) as storage, pytest.MonkeyPatch.context() as patch:
        patch.setattr(job_scoring_module, "SWEEP_LINK_CAP", cap)
        add_company_source("Acme", careers_url, storage)
        outcome = score_full_board(
            "Acme",
            workspace,
            storage,
            RecordedProvider(_judgment(quotes=[])),
            fetcher=lambda url: pages[url],
        )
    assert len(outcome.scored) == cap
    assert any("sweep cap" in note for note in outcome.notes)


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


def test_interview_packet_seeding_and_review_modes(workspace: Config) -> None:
    from wingman.application.job_scoring import INTERVIEW_AREAS, render_interview

    packet = render_interview(workspace)
    assert "SEEDING" in packet
    for name, _prompt in INTERVIEW_AREAS:
        assert name in packet  # all five areas, from the single source list
    assert "Save nothing without confirmation" in packet

    save_criteria(workspace, CRITERIA)
    packet = render_interview(workspace)
    assert "REVIEW" in packet and "No crypto" in packet  # current doc shown
    assert "Keep / Update" in packet


def test_review_due_only_when_stale(workspace: Config) -> None:
    import os
    import time

    from wingman.application.job_scoring import (
        REVIEW_EVERY_DAYS,
        criteria_age_days,
        criteria_review_due,
    )

    assert criteria_age_days(workspace) is None  # no doc: seeding hint, not a nudge
    assert criteria_review_due(workspace) is None
    save_criteria(workspace, CRITERIA)
    assert criteria_age_days(workspace) == 0
    assert criteria_review_due(workspace) is None  # fresh
    stale = time.time() - (REVIEW_EVERY_DAYS + 10) * 86400
    os.utime(criteria_path(workspace), (stale, stale))
    assert criteria_review_due(workspace) == REVIEW_EVERY_DAYS + 10


def test_stale_criteria_becomes_a_triageable_digest_action(workspace: Config) -> None:
    import os
    import time

    from wingman.application.focus import _criteria_review_action
    from wingman.application.job_scoring import REVIEW_EVERY_DAYS

    assert _criteria_review_action(workspace) is None  # no doc
    save_criteria(workspace, CRITERIA)
    assert _criteria_review_action(workspace) is None  # fresh doc
    stale = time.time() - (REVIEW_EVERY_DAYS + 1) * 86400
    os.utime(criteria_path(workspace), (stale, stale))
    action = _criteria_review_action(workspace)
    assert action is not None
    assert action.key == "criteria-review"  # snooze = cadence, mute = off (RFC-031)
    assert "Review your job criteria" in action.what
    assert any("wingman criteria review" in item for item in action.evidence)


def test_mcp_job_criteria_roundtrip(workspace: Config) -> None:
    from wingman.mcp_server import job_criteria

    Storage(workspace.db_path).close()
    assert "No job-criteria.md yet" in job_criteria("show")
    assert "SEEDING" in job_criteria("review")
    assert "Saved job-criteria.md" in job_criteria("save", text=CRITERIA)
    assert "No crypto" in job_criteria("show")
    assert "REVIEW" in job_criteria("review")  # same loop, review mode now
    assert "failed" in job_criteria("save", text="  ")
    assert "unknown action" in job_criteria("nope")
    # the docstring carries the interview protocol (RFC-025/030/031 convention)
    assert "AskUserQuestion" in (job_criteria.__doc__ or "")
    assert "confirms the wording" in (job_criteria.__doc__ or "")
