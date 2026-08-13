"""`heap sort` (#113): classify, cluster, propose — route nothing.

The heap's promise is that nothing you drop vanishes. Sorting is where that
promise is easiest to break: a misclassified drop gets routed somewhere
wrong, a merged near-namesake corrupts a person's record, an unclustered
item quietly falls off the report. Most of these tests are about those
three.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.application.heap import add_to_heap
from wingman.application.heap_sort import (
    UNCLUSTERED,
    DropKind,
    classify,
    render_sort,
    sort_heap,
)
from wingman.cli.main import app
from wingman.domain.heap import HeapHeat, HeapItem
from wingman.infrastructure.config import ENV_DATA_DIR
from wingman.infrastructure.storage import Storage


def _item(raw: str, heat: str = "warm", note: str = "") -> HeapItem:
    return HeapItem(item=raw, heat=HeapHeat(heat), note=note)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.linkedin.com/in/davehk/", DropKind.PERSON),
        ("https://github.com/lethain", DropKind.PERSON),
        ("https://jobs.lever.co/cursor/abc-123", DropKind.POSTING),
        ("https://boards.greenhouse.io/anthropic/jobs/4", DropKind.POSTING),
        ("https://cursor.com/careers/staff-engineer", DropKind.POSTING),
        ("https://acme.com/jobs/42", DropKind.POSTING),
        ("https://cursor.com", DropKind.COMPANY),
        ("https://cursor.com/", DropKind.COMPANY),
        ("https://lenny.substack.com/p/what-a-harness-is", DropKind.ARTICLE),
        ("https://acme.com/blog/how-we-ship", DropKind.ARTICLE),
        ("/home/dhk/Pictures/screenshot-2026-08-12.png", DropKind.SCREENSHOT),
        ("~/Downloads/IMG_4821.HEIC", DropKind.SCREENSHOT),
        ("some half-remembered thing about a founder", DropKind.UNKNOWN),
    ],
)
def test_each_drop_shape_is_classified_by_shape_alone(raw: str, expected: DropKind) -> None:
    """Deterministic, because routing has a correct answer — AGENTS.md
    forbids a model for exactly this, and a model could only get it wrong
    more expensively."""
    assert classify(_item(raw)).kind is expected


def test_a_person_host_beats_a_posting_marker_in_the_path() -> None:
    """'linkedin.com/in/someone/recent-activity/jobs' is a person's page,
    not an opening. Order of checks is the whole answer here."""
    result = classify(_item("https://www.linkedin.com/in/davehk/recent-activity/jobs/"))

    assert result.kind is DropKind.PERSON
    assert result.slug == "davehk"


def test_every_classification_carries_the_evidence_it_keyed_on() -> None:
    """#113 asks for classification WITH the evidence for it. 'This is a
    posting' with nothing behind it is an assertion this codebase refuses
    everywhere else."""
    for raw in (
        "https://jobs.lever.co/cursor/abc",
        "https://www.linkedin.com/in/davehk/",
        "https://cursor.com",
        "not a url at all",
    ):
        assert classify(_item(raw)).evidence


def test_an_unplaceable_drop_says_so_rather_than_being_guessed() -> None:
    result = classify(_item("call the person Trent mentioned"))

    assert result.kind is DropKind.UNKNOWN
    assert "nothing to key on" in result.evidence


def _sorted(tmp_path: Path, *drops: tuple[str, str]) -> tuple[Storage, object]:
    storage = Storage(tmp_path / "wingman.db")
    for raw, heat in drops:
        add_to_heap([raw], storage, heat=heat)
    return storage, sort_heap(storage)


def test_drops_from_one_burst_cluster_on_their_company(tmp_path: Path) -> None:
    """The motivating case: a role, the person who posted it, and the
    company site arrive together."""
    storage, report = _sorted(
        tmp_path,
        ("https://cursor.com/careers/staff-eng", "hot"),
        ("https://jobs.cursor.com/abc", "warm"),
        ("https://cursor.com", "warm"),
    )
    with storage:
        keys = [cluster.key for cluster in report.clusters]

    assert keys == ["cursor"]
    assert len(report.clusters[0].classifications) == 3


def test_a_cluster_is_as_hot_as_its_hottest_member(tmp_path: Path) -> None:
    """Not an average — one cold drop must not bury an urgent one."""
    storage, report = _sorted(
        tmp_path,
        ("https://cold.com", "cold"),
        ("https://cold.com/blog/x", "cold"),
        ("https://urgent.com", "hot"),
    )
    with storage:
        assert next(cluster.key for cluster in report.clusters) == "urgent"


def test_drops_with_no_company_signal_stay_visible(tmp_path: Path) -> None:
    """Nothing silently vanishes — the property the heap exists to protect."""
    storage, report = _sorted(
        tmp_path,
        ("https://www.linkedin.com/in/davehk/", "warm"),
        ("a thing I half remember", "warm"),
    )
    with storage:
        unclustered = next(c for c in report.clusters if c.key == UNCLUSTERED)

    assert len(unclustered.classifications) == 2
    assert report.total == 2


def test_the_unclustered_group_sorts_last_but_is_never_hidden(tmp_path: Path) -> None:
    storage, report = _sorted(
        tmp_path,
        ("https://www.linkedin.com/in/someone/", "hot"),
        ("https://acme.com", "cold"),
    )
    with storage:
        assert [cluster.key for cluster in report.clusters] == ["acme", UNCLUSTERED]


def test_linkedin_never_becomes_a_company_cluster(tmp_path: Path) -> None:
    """Clustering on the host would put every profile in one meaningless
    'linkedin' bucket."""
    storage, report = _sorted(
        tmp_path,
        ("https://www.linkedin.com/in/a/", "warm"),
        ("https://www.linkedin.com/in/b/", "warm"),
    )
    with storage:
        assert [cluster.key for cluster in report.clusters] == [UNCLUSTERED]


def test_subdomains_of_one_company_share_a_key() -> None:
    assert classify(_item("https://jobs.cursor.com/x")).company_key == "cursor"
    assert classify(_item("https://cursor.com/careers/y")).company_key == "cursor"


def test_near_namesake_slugs_are_flagged_and_neither_is_routed(tmp_path: Path) -> None:
    """#113's own example. Merging two people is the one mistake here that
    corrupts a record rather than merely mislabelling it."""
    storage, report = _sorted(
        tmp_path,
        ("https://www.linkedin.com/in/ishanagupta/", "warm"),
        ("https://www.linkedin.com/in/ishangupta/", "warm"),
    )
    with storage:
        rendered = render_sort(report)

    assert report.namesakes == [("ishanagupta", "ishangupta")]
    assert "NEAR-NAMESAKES" in rendered
    assert "two people until you say otherwise" in rendered


def test_unrelated_slugs_are_not_flagged_as_namesakes(tmp_path: Path) -> None:
    storage, report = _sorted(
        tmp_path,
        ("https://www.linkedin.com/in/davehk/", "warm"),
        ("https://www.linkedin.com/in/fubini/", "warm"),
    )
    with storage:
        assert report.namesakes == []


def test_a_screenshot_is_reported_as_unread_not_silently_dropped(tmp_path: Path) -> None:
    """Extraction needs a vision path ModelRequest does not have. Saying so
    is the honest answer; pretending to have read it is not."""
    storage, report = _sorted(tmp_path, ("/home/dhk/Pictures/lead.png", "hot"))
    with storage:
        rendered = render_sort(report)

    assert len(report.awaiting_extraction) == 1
    assert "NOT read" in rendered
    assert "stay in the heap" in rendered


def test_a_note_mentioning_a_date_is_surfaced_at_the_top(tmp_path: Path) -> None:
    """A lead that expires is the one case where sorting later has a cost —
    #113's motivating hiring-event example."""
    storage = Storage(tmp_path / "wingman.db")
    with storage:
        add_to_heap(
            ["https://acme.com/careers/x"], storage, heat="warm", note="hiring event tomorrow"
        )
        add_to_heap(["https://other.com"], storage, heat="hot")
        rendered = render_sort(sort_heap(storage))

    assert "TIME-SENSITIVE" in rendered
    # It leads the report, ahead of even the hot cluster.
    assert rendered.index("TIME-SENSITIVE") < rendered.index("── ")


def test_urgency_is_read_from_the_users_own_note_never_invented(tmp_path: Path) -> None:
    storage, report = _sorted(tmp_path, ("https://acme.com/careers/x", "hot"))
    with storage:
        assert report.time_sensitive == []


def test_the_report_names_the_command_each_drop_would_route_to(tmp_path: Path) -> None:
    """What is previewed has to be enough to disagree with, or confirming
    it is theatre."""
    storage, report = _sorted(
        tmp_path,
        ("https://jobs.lever.co/cursor/abc", "hot"),
        ("https://www.linkedin.com/in/davehk/", "warm"),
    )
    with storage:
        rendered = render_sort(report)

    assert "wingman assess --url" in rendered
    assert "wingman people add" in rendered
    assert "because:" in rendered


def test_sorting_writes_nothing_and_leaves_the_heap_alone(tmp_path: Path) -> None:
    """Explicitly invoked, and still not a mutation. Routing happens later,
    per cluster, on confirmation."""
    storage = Storage(tmp_path / "wingman.db")
    with storage:
        add_to_heap(["https://cursor.com", "https://acme.com"], storage, heat="hot")
        before = [item.item_id for item in storage.list_heap_items()]
        sort_heap(storage)
        assert [item.item_id for item in storage.list_heap_items()] == before


def test_an_empty_heap_says_so(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "wingman.db")
    with storage:
        assert "empty" in render_sort(sort_heap(storage))


def test_the_cli_sorts_without_routing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    runner = CliRunner()
    runner.invoke(app, ["init"])
    runner.invoke(app, ["heap", "add", "https://cursor.com/careers/x", "--heat", "hot"])

    result = runner.invoke(app, ["heap", "sort"])

    assert result.exit_code == 0, result.output
    assert "NOTHING is routed until you confirm" in result.output
    assert "cursor" in result.output


def test_the_mcp_twin_exposes_sort() -> None:
    """CLI/MCP parity (RFC-008): a capability on one surface and not the
    other is the gap that sends people back to the terminal."""
    import inspect

    from wingman import mcp_server

    source = inspect.getsource(mcp_server.heap)
    assert '"sort"' in source
    assert "render_sort" in source
