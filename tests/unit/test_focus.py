"""Follow + overnight (RFC-018): enrollment is consent; the digest is honest."""

from pathlib import Path

import pytest

from wingman.application.focus import (
    OVERNIGHT_LIST,
    follow_company,
    overnight_run,
    render_follow_report,
)
from wingman.application.ingest import IngestError
from wingman.application.people import add_person
from wingman.application.research import list_company_sources
from wingman.infrastructure.config import load_config
from wingman.infrastructure.fetch import FetchError
from wingman.infrastructure.storage import Storage

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><item>
    <title>On Evals</title>
    <link>https://jane.substack.com/p/on-evals</link>
    <content:encoded><![CDATA[<p>Evaluations before autonomy, always.</p>]]></content:encoded>
  </item></channel></rss>
"""

CAREERS_PAGE = b'<html><body><a href="/jobs/researcher">Researcher</a></body></html>'


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    config.models_config_path.write_text(
        '[models.embed_semantic]\nprovider = "hashed"\n', encoding="utf-8"
    )
    return tmp_path


def test_follow_enrolls_company_and_sourced_people(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage, substack_url="https://jane.substack.com", company="Acme")
        add_person("Quiet Contact", storage, company="Acme")
        add_person("Other Person", storage, company="Elsewhere")

        def probe(url: str) -> bytes:
            if url.endswith("/careers") or url.endswith("/blog"):
                return CAREERS_PAGE
            raise FetchError("404")

        report = follow_company("acme", storage, url="https://acme.example.com", fetcher=probe)
        assert report.company == "Acme" and report.company_enrolled
        assert report.people_enrolled == ["Jane Author"]  # has writing attached
        assert report.known_without_sources == ["Quiet Contact"]  # suggested, not enrolled
        assert report.sources_approved == [
            "https://acme.example.com/careers",
            "https://acme.example.com/blog",
        ]
        assert {u.url for u in list_company_sources("Acme", storage)} == set(
            report.sources_approved
        )
        members = storage.watchlist_members(OVERNIGHT_LIST)
        assert ("company", "Acme") in members and ("person", "Jane Author") in members
        assert ("person", "Quiet Contact") not in members
        assert ("person", "Other Person") not in members

        # idempotent: following again enrolls and approves nothing new
        again = follow_company("Acme", storage, url="https://acme.example.com", fetcher=probe)
        assert not again.company_enrolled
        assert again.people_enrolled == [] and again.sources_approved == []
        rendered = render_follow_report(report)
        assert "Jane Author" in rendered and "wingman overnight" in rendered


def test_follow_rejects_bad_input(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="empty"):
            follow_company("  ", storage)
        with pytest.raises(IngestError, match="https"):
            follow_company("Acme", storage, url="http://acme.example.com")


def test_overnight_requires_enrollment(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="follow"):
            overnight_run(config, storage)


def test_overnight_runs_targets_and_writes_digest(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.application.news as news_module
    import wingman.application.people as people_module
    import wingman.application.research as research_module

    config = load_config()
    monkeypatch.setattr(people_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(news_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(research_module, "fetch_url", lambda url: CAREERS_PAGE)
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage, substack_url="https://jane.substack.com", company="Acme")
        follow_company(
            "Acme", storage, url="https://acme.example.com", fetcher=lambda url: CAREERS_PAGE
        )
        report = overnight_run(config, storage)

    assert report.processed == 2  # the company and Jane
    kinds = {(target.name, target.kind) for target in report.targets}
    assert kinds == {("Acme", "company"), ("Jane Author", "person")}
    digest = Path(report.digest_path)
    assert digest.exists() and "digests" in str(digest)
    text = digest.read_text(encoding="utf-8")
    assert "# Overnight digest" in text
    assert "Acme (company)" in text and "Jane Author (person)" in text
    assert "research https://acme.example.com/careers" in text
    # no synthesize model in tests: model steps skipped honestly, run not fatal
    assert "themes skipped" in text
    assert "fetch: ok" in text
    # the action list closes the digest: fresh writing -> a read/outreach action
    assert "## Action list" in text
    assert text.index("## Action list") > text.index("Jane Author (person)")
    assert "Read Jane Author's new writing" in text
    assert "why: 1 new post(s) fetched overnight" in text
    assert "evidence: [On Evals](https://jane.substack.com/p/on-evals)" in text
    # latest.md mirrors the newest digest
    latest = digest.parent / "latest.md"
    assert latest.exists() and latest.read_text(encoding="utf-8") == text


def test_overnight_reports_failures_without_dying(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.application.news as news_module
    import wingman.application.people as people_module
    import wingman.application.research as research_module

    def boom(url: str) -> bytes:
        raise FetchError("network down")

    config = load_config()
    monkeypatch.setattr(people_module, "fetch_url", boom)
    monkeypatch.setattr(news_module, "fetch_url", boom)
    monkeypatch.setattr(research_module, "fetch_url", boom)
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage, substack_url="https://jane.substack.com", company="Acme")
        follow_company("Acme", storage)  # no url: no sources approved
        report = overnight_run(config, storage)
    text = Path(report.digest_path).read_text(encoding="utf-8")
    assert "research skipped" in text  # no approved sources, said so
    assert "fetch: failed" in text  # feed failure reported per-step
    assert report.processed == 2


def test_second_run_actions_carry_new_link_evidence(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.application.news as news_module
    import wingman.application.people as people_module
    import wingman.application.research as research_module

    config = load_config()
    monkeypatch.setattr(people_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(news_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(research_module, "fetch_url", lambda url: CAREERS_PAGE)
    grown = (
        b'<html><body><a href="/jobs/researcher">R</a><a href="/jobs/staff-mle">S</a></body></html>'
    )
    with Storage(config.db_path) as storage:
        follow_company(
            "Acme",
            storage,
            url="https://acme.example.com",
            fetcher=lambda url: (
                CAREERS_PAGE
                if url.endswith("/careers")
                else (_ for _ in ()).throw(FetchError("404"))
            ),
        )
        overnight_run(config, storage)  # baseline snapshot
        monkeypatch.setattr(research_module, "fetch_url", lambda url: grown)
        report = overnight_run(config, storage)
    action = next(a for a in report.actions if "opening" in a.what)
    assert action.who == "Acme"
    assert "1 new link since" in action.why
    assert action.evidence == [
        "[link](https://acme.example.com/jobs/staff-mle)",
        'run: wingman assess --url "https://acme.example.com/jobs/staff-mle"',
    ]
    text = Path(report.digest_path).read_text(encoding="utf-8")
    assert "**Assess the new opening(s) at Acme**" in text


def test_new_link_shared_by_two_sources_is_one_action_not_two(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A company's /careers and /blog pages both linking the same new post
    must surface once, not once per source (overnight P3 — openai.com's
    /news, /newsroom, and /about all surfaced the same link in one run)."""
    import wingman.application.news as news_module
    import wingman.application.people as people_module
    import wingman.application.research as research_module

    config = load_config()
    monkeypatch.setattr(people_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(news_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(research_module, "fetch_url", lambda url: CAREERS_PAGE)
    grown = (
        b'<html><body><a href="/jobs/researcher">R</a><a href="/jobs/staff-mle">S</a></body></html>'
    )
    with Storage(config.db_path) as storage:
        # both /careers and /blog are approved as research sources, and both
        # happen to link the same new posting once it appears
        follow_company(
            "Acme", storage, url="https://acme.example.com", fetcher=lambda url: CAREERS_PAGE
        )
        overnight_run(config, storage)  # baseline snapshot for both sources
        monkeypatch.setattr(research_module, "fetch_url", lambda url: grown)
        report = overnight_run(config, storage)
    opening_actions = [a for a in report.actions if "opening" in a.what]
    assert len(opening_actions) == 1
    assert opening_actions[0].evidence == [
        "[link](https://acme.example.com/jobs/staff-mle)",
        'run: wingman assess --url "https://acme.example.com/jobs/staff-mle"',
    ]


def test_themes_failure_flips_target_status_instead_of_being_swallowed(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real themes-generation failure (e.g. the Anthropic auth error from
    overnight P1) must mark the company target failed, not report '✓' with
    the error tucked away in a 'themes skipped' line."""
    import wingman.application.news as news_module
    import wingman.application.people as people_module
    import wingman.application.pov as pov_module
    import wingman.application.research as research_module

    config = load_config()
    config.models_config_path.write_text(
        '[models.embed_semantic]\nprovider = "hashed"\n'
        '[models.synthesize_balanced]\nprovider = "recorded"\npath = "'
        + str(config.data_dir / "recorded.json")
        + '"\n',
        encoding="utf-8",
    )
    (config.data_dir / "recorded.json").write_text('{"items": []}', encoding="utf-8")
    monkeypatch.setattr(people_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(news_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(research_module, "fetch_url", lambda url: CAREERS_PAGE)

    def broken_themes(*args: object, **kwargs: object) -> object:
        raise RuntimeError("Could not resolve authentication method")

    monkeypatch.setattr(pov_module, "build_company_pov", broken_themes)
    with Storage(config.db_path) as storage:
        follow_company(
            "Acme", storage, url="https://acme.example.com", fetcher=lambda url: CAREERS_PAGE
        )
        report = overnight_run(config, storage)
    acme = next(t for t in report.targets if t.name == "Acme")
    assert acme.status == "failed"
    assert any("themes failed" in line for line in acme.lines)


def test_latest_digest_and_out_dir(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from wingman.application.focus import latest_digest

    import wingman.application.news as news_module
    import wingman.application.people as people_module

    config = load_config()
    assert latest_digest(config) is None
    monkeypatch.setattr(people_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(news_module, "fetch_url", lambda url: FEED)
    custom = Path(str(workspace)) / "desk"
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage, substack_url="https://jane.substack.com")
        storage.watchlist_add(OVERNIGHT_LIST, "person", "Jane Author")
        report = overnight_run(config, storage, out_dir=custom)
    assert Path(report.digest_path).parent == custom.resolve()
    assert (custom / "latest.md").exists()
    # default-location helper ignores custom out_dirs (they are the user's copy)
    assert latest_digest(config) is None


def test_tickler_fires_on_fresh_material_citing_objective(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RFC-037: a person with an objective and fresh writing this run earns
    a relationship: tickler action citing the objective's own words."""
    import wingman.application.news as news_module
    import wingman.application.people as people_module
    from wingman.application.relationship import save_objective

    config = load_config()
    monkeypatch.setattr(people_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(news_module, "fetch_url", lambda url: FEED)
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage, substack_url="https://jane.substack.com")
        storage.watchlist_add(OVERNIGHT_LIST, "person", "Jane Author")
        save_objective(
            "Jane Author",
            "Build a peer relationship.",
            "She responds well to direct technical exchange.",
            "Reply to her next post with a real technical take.",
            storage,
        )
        report = overnight_run(config, storage)
    keys = {action.key for action in report.actions}
    assert "relationship:jane author" in keys
    tickler = next(a for a in report.actions if a.key == "relationship:jane author")
    assert "Reply to her next post" in tickler.what
    assert "direct technical exchange" in tickler.why
    text = Path(report.digest_path).read_text(encoding="utf-8")
    assert "relationship:jane author" in text


def test_tickler_fires_on_stale_next_move_without_fresh_material(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No new writing this run, but the objective hasn't moved in a
    while: the tickler still fires, citing staleness instead."""
    from datetime import UTC, datetime, timedelta

    from wingman.application.focus import TICKLER_STALE_DAYS
    from wingman.application.relationship import save_objective

    def empty_feed(url: str) -> bytes:
        return b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'

    config = load_config()
    import wingman.application.news as news_module
    import wingman.application.people as people_module

    monkeypatch.setattr(people_module, "fetch_url", empty_feed)
    monkeypatch.setattr(news_module, "fetch_url", empty_feed)
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage, substack_url="https://jane.substack.com")
        storage.watchlist_add(OVERNIGHT_LIST, "person", "Jane Author")
        objective = save_objective("Jane Author", "Goal.", "Thesis.", "Next move.", storage)
        backdated = objective.model_copy(
            update={"updated_at": datetime.now(UTC) - timedelta(days=TICKLER_STALE_DAYS + 1)}
        )
        storage.save_objective(backdated)
        report = overnight_run(config, storage)
    tickler = next(a for a in report.actions if a.key == "relationship:jane author")
    assert "hasn't moved in" in tickler.why


def test_tickler_staleness_boundary_day_before_and_of(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exact-boundary regression: TICKLER_STALE_DAYS - 1 days old must not
    fire on staleness alone, and exactly TICKLER_STALE_DAYS days old must —
    the same boundary-precision convention this codebase's other staleness
    checks are held to (e.g. test_news.py's staleness-boundary test)."""
    from datetime import UTC, datetime, timedelta

    from wingman.application.focus import TICKLER_STALE_DAYS
    from wingman.application.relationship import save_objective

    def empty_feed(url: str) -> bytes:
        return b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'

    config = load_config()
    import wingman.application.news as news_module
    import wingman.application.people as people_module

    monkeypatch.setattr(people_module, "fetch_url", empty_feed)
    monkeypatch.setattr(news_module, "fetch_url", empty_feed)
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage, substack_url="https://jane.substack.com")
        storage.watchlist_add(OVERNIGHT_LIST, "person", "Jane Author")
        objective = save_objective("Jane Author", "Goal.", "Thesis.", "Next move.", storage)
        storage.save_objective(
            objective.model_copy(
                update={"updated_at": datetime.now(UTC) - timedelta(days=TICKLER_STALE_DAYS - 1)}
            )
        )
        report = overnight_run(config, storage)
    assert not any(action.key == "relationship:jane author" for action in report.actions)

    with Storage(config.db_path) as storage:
        objective = storage.list_objectives()[0]
        storage.save_objective(
            objective.model_copy(
                update={"updated_at": datetime.now(UTC) - timedelta(days=TICKLER_STALE_DAYS)}
            )
        )
        report = overnight_run(config, storage)
    assert "relationship:jane author" in {action.key for action in report.actions}


def test_no_tickler_without_objective(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unchanged behavior: no objective, no relationship: action."""
    import wingman.application.news as news_module
    import wingman.application.people as people_module

    config = load_config()
    monkeypatch.setattr(people_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(news_module, "fetch_url", lambda url: FEED)
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage, substack_url="https://jane.substack.com")
        storage.watchlist_add(OVERNIGHT_LIST, "person", "Jane Author")
        report = overnight_run(config, storage)
    assert not any(action.key.startswith("relationship:") for action in report.actions)


def test_tickler_mute_suppresses_future_digests(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RFC-031: muting the relationship: key suppresses the tickler, same
    as any other digest action — snooze is the tickler's cadence."""
    import wingman.application.news as news_module
    import wingman.application.people as people_module
    from wingman.application.relationship import save_objective
    from wingman.application.triage import mute_action

    config = load_config()
    monkeypatch.setattr(people_module, "fetch_url", lambda url: FEED)
    monkeypatch.setattr(news_module, "fetch_url", lambda url: FEED)
    with Storage(config.db_path) as storage:
        add_person("Jane Author", storage, substack_url="https://jane.substack.com")
        storage.watchlist_add(OVERNIGHT_LIST, "person", "Jane Author")
        save_objective("Jane Author", "Goal.", "Thesis.", "Next move.", storage)
        mute_action("relationship:jane author", storage)
        report = overnight_run(config, storage)
    assert not any(action.key == "relationship:jane author" for action in report.actions)


def test_relationship_review_fires_for_stale_objective_off_watchlist(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RFC-037 pt 4: an objective going stale earns a relationship-review:
    action even for a person NOT enrolled on the overnight watchlist —
    the review is about the objective's age, not fresh material."""
    from datetime import UTC, datetime, timedelta

    from wingman.application.relationship import REVIEW_EVERY_DAYS, save_objective

    config = load_config()
    with Storage(config.db_path) as storage:
        # Someone enrolled, so overnight_run has something to process.
        add_person("Watched Person", storage)
        storage.watchlist_add(OVERNIGHT_LIST, "person", "Watched Person")
        # An objective for someone else, off the watchlist entirely.
        add_person("Brandon Galang", storage)
        objective = save_objective("Brandon Galang", "Goal.", "Thesis.", "Next move.", storage)
        backdated = objective.model_copy(
            update={"updated_at": datetime.now(UTC) - timedelta(days=REVIEW_EVERY_DAYS + 1)}
        )
        storage.save_objective(backdated)
        report = overnight_run(config, storage)
    keys = {action.key for action in report.actions}
    assert "relationship-review:brandon galang" in keys
    assert "relationship:brandon galang" not in keys  # never processed: no tickler
    review = next(a for a in report.actions if a.key == "relationship-review:brandon galang")
    assert "strengthened, stalled, or was the thesis wrong" in review.why


def test_no_relationship_review_when_objective_fresh(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.application.relationship import save_objective

    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Watched Person", storage)
        storage.watchlist_add(OVERNIGHT_LIST, "person", "Watched Person")
        add_person("Brandon Galang", storage)
        save_objective("Brandon Galang", "Goal.", "Thesis.", "Next move.", storage)
        report = overnight_run(config, storage)
    assert not any(action.key.startswith("relationship-review:") for action in report.actions)


def test_relationship_review_boundary_day_before_and_of(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exact-boundary regression: REVIEW_EVERY_DAYS - 1 days old must not
    fire yet, and exactly REVIEW_EVERY_DAYS days old must fire — the same
    boundary-precision convention this codebase's other staleness checks
    are held to (e.g. test_news.py's staleness-boundary test)."""
    from datetime import UTC, datetime, timedelta

    from wingman.application.relationship import REVIEW_EVERY_DAYS, save_objective

    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Watched Person", storage)
        storage.watchlist_add(OVERNIGHT_LIST, "person", "Watched Person")
        add_person("Brandon Galang", storage)
        objective = save_objective("Brandon Galang", "Goal.", "Thesis.", "Next move.", storage)
        storage.save_objective(
            objective.model_copy(
                update={"updated_at": datetime.now(UTC) - timedelta(days=REVIEW_EVERY_DAYS - 1)}
            )
        )
        report = overnight_run(config, storage)
    assert not any(action.key.startswith("relationship-review:") for action in report.actions)

    with Storage(config.db_path) as storage:
        objective = storage.list_objectives()[0]
        storage.save_objective(
            objective.model_copy(
                update={"updated_at": datetime.now(UTC) - timedelta(days=REVIEW_EVERY_DAYS)}
            )
        )
        report = overnight_run(config, storage)
    assert "relationship-review:brandon galang" in {action.key for action in report.actions}


def test_relationship_review_mute_suppresses(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from wingman.application.relationship import REVIEW_EVERY_DAYS, save_objective
    from wingman.application.triage import mute_action

    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Watched Person", storage)
        storage.watchlist_add(OVERNIGHT_LIST, "person", "Watched Person")
        add_person("Brandon Galang", storage)
        objective = save_objective("Brandon Galang", "Goal.", "Thesis.", "Next move.", storage)
        storage.save_objective(
            objective.model_copy(
                update={"updated_at": datetime.now(UTC) - timedelta(days=REVIEW_EVERY_DAYS + 1)}
            )
        )
        mute_action("relationship-review:brandon galang", storage)
        report = overnight_run(config, storage)
    assert not any(action.key == "relationship-review:brandon galang" for action in report.actions)


def test_heap_hot_action_fires_and_mute_suppresses(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#113: hot heap items sitting unsorted earn one digest nudge; cold
    ones never do, and the nudge rides ordinary RFC-031 triage."""
    from wingman.application.heap import add_to_heap
    from wingman.application.triage import mute_action

    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Watched Person", storage)
        storage.watchlist_add(OVERNIGHT_LIST, "person", "Watched Person")
        add_to_heap(["https://x.example/cold"], storage, heat="cold")
        report = overnight_run(config, storage)
    assert not any(action.key == "heap-hot" for action in report.actions)

    with Storage(config.db_path) as storage:
        add_to_heap(["https://x.example/hot"], storage, heat="hot")
        report = overnight_run(config, storage)
    heap_action = next(a for a in report.actions if a.key == "heap-hot")
    assert "1 hot lead" in heap_action.why

    with Storage(config.db_path) as storage:
        mute_action("heap-hot", storage)
        report = overnight_run(config, storage)
    assert not any(action.key == "heap-hot" for action in report.actions)


def test_titled_link_resolves_sanitizes_and_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """#109: evidence links carry the page's own title; every failure mode
    (no title, fetch error, spent budget) degrades to the old bare link."""
    import wingman.application.research as research_module
    from wingman.application.focus import _TitleBudget, _titled_link

    page = (
        b"<html><head><title>  Staff [ML] Engineer\n at Acme  </title></head><body>x</body></html>"
    )
    monkeypatch.setattr(research_module, "fetch_url", lambda url: page)
    budget = _TitleBudget(remaining=3)
    assert (
        _titled_link("https://a.example/j", budget)
        == "[Staff (ML) Engineer at Acme](https://a.example/j)"  # brackets sanitized
    )
    monkeypatch.setattr(research_module, "fetch_url", lambda url: b"<html><body>no</body></html>")
    assert _titled_link("https://a.example/k", budget) == "[link](https://a.example/k)"
    monkeypatch.setattr(
        research_module, "fetch_url", lambda url: (_ for _ in ()).throw(FetchError("451"))
    )
    assert _titled_link("https://a.example/l", budget) == "[link](https://a.example/l)"
    assert budget.remaining == 0
    monkeypatch.setattr(research_module, "fetch_url", lambda url: page)
    assert _titled_link("https://a.example/m", budget) == "[link](https://a.example/m)"  # spent
