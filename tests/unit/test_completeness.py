"""Completeness: deterministic counts across the four measurable sections."""

from pathlib import Path

import pytest

from wingman.application.completeness import (
    compute_completeness,
    ensure_following_backfilled,
    next_actions,
)
from wingman.application.job_scoring import save_criteria
from wingman.application.people import FOLLOWING_LIST, add_person, attach_feed, follow_person
from wingman.application.relationship import log_interaction
from wingman.domain.person import FeedSource, Person, PersonOrigin
from wingman.domain.profile import ClaimClassification, EvidenceSpan, ProfileItem, ProfileItemKind
from wingman.domain.relationship import RelationshipObjective
from wingman.domain.source_record import SourceRecord
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.reporting.completeness import (
    MAX_COMPANIES_LISTED,
    MAX_PEOPLE_LISTED,
    render_completeness_markdown,
    write_completeness,
)
from wingman.reporting.completeness_html import render_completeness_html


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return tmp_path


def _add_profile_item(storage: Storage, kind: ProfileItemKind, name: str) -> None:
    record = SourceRecord(
        source_type="test",
        source_locator="test://fixture",
        content_hash=f"hash-{name}",
    )
    storage.add_source_record(record)
    storage.add_profile_item(
        ProfileItem(
            kind=kind,
            name=name,
            detail="",
            classification=ClaimClassification.FACT,
            confidence=1.0,
            evidence=[EvidenceSpan(source_record_id=record.record_id, quote=name)],
            prompt_version="test",
            extracted_by="test",
        )
    )


def test_empty_workspace_reports_all_zero(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        report = compute_completeness(storage, config)
    assert report.career.roles == 0
    assert report.career.achievements == 0
    assert report.career.skills == 0
    assert report.career.testimonials == 0
    assert report.job_criteria.exists is False
    assert report.people == []
    assert report.companies == []


def test_career_profile_counts_by_kind(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _add_profile_item(storage, ProfileItemKind.ROLE, "Head of Data")
        _add_profile_item(storage, ProfileItemKind.SKILL, "SQL")
        _add_profile_item(storage, ProfileItemKind.SKILL, "Python")
        report = compute_completeness(storage, config)
    assert report.career.roles == 1
    assert report.career.skills == 2
    assert report.career.achievements == 0
    assert report.career.testimonials == 0


def test_job_criteria_exists_after_save(workspace: Path) -> None:
    config = load_config()
    save_criteria(config, "Must-haves: remote-friendly.")
    with Storage(config.db_path) as storage:
        report = compute_completeness(storage, config)
    assert report.job_criteria.exists is True


def test_people_reflect_links_and_log_entries(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Jeff Lim", storage, email="jeff.lim@example.com")
        add_person(
            "Cait Collins",
            storage,
            linkedin_url="https://www.linkedin.com/in/example",
            company="Example Co",
        )
        log_interaction("Cait Collins", "Coffee, reconnecting.", config, storage)
        report = compute_completeness(storage, config)
    by_name = {person.name: person for person in report.people}
    assert by_name["Jeff Lim"].linked is False
    assert by_name["Jeff Lim"].log_entries == 0
    assert by_name["Cait Collins"].linked is True
    assert by_name["Cait Collins"].log_entries == 1
    assert by_name["Cait Collins"].company == "Example Co"


def _settled(storage: Storage) -> None:
    """Run the one-time #624 backfill on the empty workspace, so a test sees the
    steady-state rule (only watchlist members are followed) rather than the
    first-call migration."""
    ensure_following_backfilled(storage)


def test_company_rows_are_followed_companies(workspace: Path) -> None:
    """#624: a company gets a row because it is followed, not because a
    followed person happens to work there."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _settled(storage)
        storage.watchlist_add("overnight", "company", "The DataFace")
        for name, company in (
            ("Jack Beckwith", "The DataFace"),
            ("Michael Hester", "The DataFace"),
            ("Olive Other", "Other Co"),
        ):
            person, _ = add_person(name, storage, company=company)
            follow_person(storage, person)
        report = compute_completeness(storage, config)
    assert [company.name for company in report.companies] == ["The DataFace"]
    company = report.companies[0]
    assert company.people_watched == 2
    assert company.pov_cards == 0
    assert company.missing_pov_cards == 2
    assert "Olive Other" in {person.name for person in report.people}


def test_a_followed_company_with_nobody_followed_there_still_appears(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _settled(storage)
        storage.watchlist_add("targets", "company", "Acme")
        report = compute_completeness(storage, config)
    assert [(c.name, c.people_watched) for c in report.companies] == [("Acme", 0)]


def test_companies_dedup_by_key_not_raw_string(workspace: Path) -> None:
    """'Acme' and 'ACME' normalize to the same company_key and must land in
    one row, not two duplicate, double-counted ones (review comment on #313)."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _settled(storage)
        storage.watchlist_add("overnight", "company", "Acme")
        storage.watchlist_add("targets", "company", "ACME")
        for name, company in (("Alice", "Acme"), ("Bob", "ACME")):
            person, _ = add_person(name, storage, company=company)
            follow_person(storage, person)
        report = compute_completeness(storage, config)
    assert len(report.companies) == 1
    assert report.companies[0].people_watched == 2


def test_write_completeness_writes_json_and_markdown(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Ruth Katz", storage)
        report, json_path, md_path = write_completeness(storage, config)
    assert json_path.exists()
    assert md_path.exists()
    assert "Ruth Katz" in md_path.read_text(encoding="utf-8")
    assert "Ruth Katz" in render_completeness_markdown(report)


def test_nothing_is_reported_blocked_that_has_since_shipped(workspace: Path) -> None:
    """This test used to assert the report SAID interview read-back and
    opportunity listing were impossible. They shipped — #311 as
    interview_status, #312 as opportunities_list — and the claim outlived
    them, so the one place somebody looks for progress declared the data
    unavailable while the tools sat right there.

    What it guards now is the shape rather than the content: a blocked
    entry must name a real, still-open reason."""
    config = load_config()
    with Storage(config.db_path) as storage:
        report = compute_completeness(storage, config)
    markdown = render_completeness_markdown(report)
    html = render_completeness_html(report)

    assert "#311" not in markdown
    assert "#312" not in markdown
    assert "no tool reads back" not in markdown
    for text in (markdown, html):
        assert "Interview" in text
        assert "Applications" in text
    assert "<!doctype html>" in html


def test_the_interview_section_reports_every_subtype_against_its_real_cap(
    workspace: Path,
) -> None:
    """6 per subtype is application.interview's own cap, so this is a
    fraction the code actually enforces rather than a denominator invented
    to fill a progress bar."""
    from wingman.application.interview import VALID_SUBTYPES, _max_submissions_per_subtype

    config = load_config()
    with Storage(config.db_path) as storage:
        report = compute_completeness(storage, config)

    assert {row.subtype for row in report.interview} == VALID_SUBTYPES
    cap = _max_submissions_per_subtype()
    assert all(row.cap == cap for row in report.interview)
    assert f"0/{cap}" in render_completeness_markdown(report)


def test_next_actions_lead_with_job_criteria(workspace: Path) -> None:
    """Ordered by what unblocks the most, not by what is emptiest. A
    workspace can be full of everything else and still not tell you which
    job to look at."""
    from wingman.application.completeness import compute_completeness, next_actions

    config = load_config()
    with Storage(config.db_path) as storage:
        actions = next_actions(compute_completeness(storage, config))

    assert actions
    assert actions[0].title == "Set your job criteria"
    assert "unscored" in actions[0].why
    assert actions[0].how == "say: let's set up my job criteria"


def test_every_action_says_what_the_gap_costs_and_how_to_close_it(workspace: Path) -> None:
    """'Testimonials: 0' is a number; the reason to care is the point."""
    from wingman.application.completeness import compute_completeness, next_actions

    config = load_config()
    with Storage(config.db_path) as storage:
        actions = next_actions(compute_completeness(storage, config))

    for action in actions:
        assert action.why.strip() and action.how.strip()
        assert action.why != action.title


def test_the_values_target_is_the_real_one_not_an_invented_denominator(
    workspace: Path,
) -> None:
    """application.values refuses below these thresholds, so 'N of 6 across 2
    kinds' is the actual contract — the distinction that keeps this from
    being a made-up percentage."""
    from wingman.application.completeness import compute_completeness
    from wingman.application.values import MIN_ITEMS, MIN_SUBTYPES

    config = load_config()
    with Storage(config.db_path) as storage:
        report = compute_completeness(storage, config)

    assert report.values.min_items == MIN_ITEMS
    assert report.values.min_subtypes == MIN_SUBTYPES
    assert report.values.captures == 0
    assert not report.values.ready
    assert not report.values.profile_built


def test_the_rendered_report_leads_with_things_to_do(workspace: Path) -> None:
    from wingman.application.completeness import compute_completeness
    from wingman.reporting.completeness import render_completeness_markdown
    from wingman.reporting.completeness_html import render_completeness_html

    config = load_config()
    with Storage(config.db_path) as storage:
        report = compute_completeness(storage, config)

    markdown = render_completeness_markdown(report)
    html = render_completeness_html(report)

    assert markdown.index("Things to do") < markdown.index("Career Profile")
    assert html.index("Things to do") < html.index("Career Profile")
    assert "let's set up my job criteria" in markdown
    assert "let&#x27;s set up my job criteria" in html or "let's set up my job criteria" in html


def test_next_actions_do_not_recommend_a_values_profile_without_inference(
    workspace: Path,
) -> None:
    """#514, reported by a hosted tenant: 13 captures across 6 kinds, well
    clear of the floor, and the top recommendation was 'Build your values
    profile' — the one call that could not succeed, because the workspace
    had no model key and nobody had funded it. Evidence readiness and
    inference availability are different questions and the list has to ask
    both."""
    from wingman.application.completeness import next_actions

    config = load_config()
    with Storage(config.db_path) as storage:
        report = compute_completeness(storage, config)
    report.values.captures = report.values.min_items
    report.values.subtypes = report.values.min_subtypes
    report.values.profile_built = False
    assert report.values.ready

    report.inference_available = True
    assert "Build your values profile" in [action.title for action in next_actions(report)]

    report.inference_available = False
    titles = [action.title for action in next_actions(report)]
    assert "Build your values profile" not in titles
    assert "Add a model key to unlock your values profile" in titles


def _import_contact(storage: Storage, name: str, company: str | None = None) -> Person:
    """A person exactly as a LinkedIn connections import creates them: no feed,
    no log, no deliberate act by the owner."""
    person = Person(name=name, origin=PersonOrigin.LINKEDIN_CONNECTIONS, company=company)
    storage.add_person(person)
    return person


def _followed(storage: Storage, name: str, company: str | None = None) -> Person:
    person, _ = add_person(name, storage, company=company)
    follow_person(storage, person)
    return person


def test_only_watchlist_members_are_measured(workspace: Path) -> None:
    """#624: completeness measures the follow set. An import, a hand-added
    record nobody followed, a log entry, an objective or a dossier does not
    make someone followed; a watchlist entry does."""
    from wingman.application.dossier_research import save_person_dossier

    config = load_config()
    with Storage(config.db_path) as storage:
        _settled(storage)
        _followed(storage, "Followed Fay", company="Acme")
        storage.watchlist_add("overnight", "person", "Overnight Otto")
        add_person("Overnight Otto", storage)
        add_person("Unfollowed Ursula", storage)
        _import_contact(storage, "Logged Lou")
        log_interaction("Logged Lou", "Coffee.", config, storage)
        objective_target = _import_contact(storage, "Objective Ola")
        storage.save_objective(
            RelationshipObjective(
                person_id=objective_target.person_id, goal="g", thesis="t", next_move="n"
            )
        )
        _import_contact(storage, "Researched Rae")
        save_person_dossier("Researched Rae", "Approved findings.", storage)
        for index in range(25):
            _import_contact(storage, f"Imported {index}", company="Initech")
        report = compute_completeness(storage, config)
        titles = [action.title for action in next_actions(report)]

    assert sorted(person.name for person in report.people) == ["Followed Fay", "Overnight Otto"]
    assert report.unfollowed_people == 29
    assert report.companies == []
    assert not any("29" in title or "27" in title for title in titles)


def test_removing_someone_from_every_list_unfollows_them(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _settled(storage)
        _followed(storage, "Fay")
        storage.watchlist_add("overnight", "person", "Fay")
        storage.watchlist_remove(FOLLOWING_LIST, "person", "Fay")
        assert [p.name for p in compute_completeness(storage, config).people] == ["Fay"]
        storage.watchlist_remove("overnight", "person", "Fay")
        report = compute_completeness(storage, config)
    assert report.people == []
    assert report.unfollowed_people == 1


def test_the_backfill_follows_everyone_the_previous_rule_measured(workspace: Path) -> None:
    """Upgrading must not silently empty the People section: everyone #601's
    derived rule listed is put on FOLLOWING_LIST on the first run."""
    from wingman.application.dossier_research import save_person_dossier

    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Hand Added Hal", storage)
        _import_contact(storage, "Logged Lou")
        log_interaction("Logged Lou", "Coffee.", config, storage)
        objective_target = _import_contact(storage, "Objective Ola")
        storage.save_objective(
            RelationshipObjective(
                person_id=objective_target.person_id, goal="g", thesis="t", next_move="n"
            )
        )
        _import_contact(storage, "Researched Rae")
        save_person_dossier("Researched Rae", "Approved findings.", storage)
        fed = _import_contact(storage, "Fed Fran")
        attach_feed(fed, FeedSource(url="https://fran.example/feed"), storage)
        _import_contact(storage, "Untouched Uma")
        report = compute_completeness(storage, config)
        following = {name for _kind, name in storage.watchlist_members(FOLLOWING_LIST)}

    expected = {"Hand Added Hal", "Logged Lou", "Objective Ola", "Researched Rae", "Fed Fran"}
    assert following == expected
    assert {person.name for person in report.people} == expected
    assert report.unfollowed_people == 1
    assert fed.person_id


def test_the_backfill_runs_once_and_never_re_adds_someone_removed(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Hal", storage)
        assert ensure_following_backfilled(storage) == 1
        storage.watchlist_remove(FOLLOWING_LIST, "person", "Hal")
        assert ensure_following_backfilled(storage) is None
        add_person("Later Lee", storage)
        report = compute_completeness(storage, config)
        assert storage.watchlist_members(FOLLOWING_LIST) == []
    assert report.people == []
    assert report.unfollowed_people == 2


def test_the_people_heading_counts_followed_people(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _settled(storage)
        _followed(storage, "Fay")
        for index in range(3):
            _import_contact(storage, f"Imported {index}")
        report = compute_completeness(storage, config)
    markdown = render_completeness_markdown(report)
    html = render_completeness_html(report)
    assert "## People (1 followed)" in markdown
    assert "People (1 followed)" in html
    assert "3 other people you are not following" in markdown
    assert "3 other people you are not following" in html


def test_the_report_stays_bounded_however_many_people_are_imported(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _settled(storage)
        for index in range(2000):
            _import_contact(storage, f"Contact {index}", company=f"Company {index % 400}")
        report = compute_completeness(storage, config)

    markdown = render_completeness_markdown(report)
    html = render_completeness_html(report)
    assert len(markdown) < 8_000
    assert len(html) < 40_000
    assert "2000 other people you are not following" in markdown
    assert "Contact 17" not in markdown
    assert report.companies == []


def test_followed_people_are_capped_in_the_rendered_report(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _settled(storage)
        for index in range(MAX_PEOPLE_LISTED + 7):
            _followed(storage, f"Person {index:03d}")
        report = compute_completeness(storage, config)

    assert len(report.people) == MAX_PEOPLE_LISTED + 7
    assert "…and 7 more" in render_completeness_markdown(report)
    assert "…and 7 more" in render_completeness_html(report)


def test_companies_are_capped_when_many_companies_are_followed(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _settled(storage)
        for index in range(2000):
            storage.watchlist_add("targets", "company", f"Company {index:04d}")
        report = compute_completeness(storage, config)

    assert len(report.companies) == 2000
    more = 2000 - MAX_COMPANIES_LISTED
    markdown = render_completeness_markdown(report)
    html = render_completeness_html(report)
    assert f"…and {more} more" in markdown
    assert f"…and {more} more" in html
    assert "Company 1999" not in markdown
    assert "Company 1999" not in html
    assert "## Companies (2000 attributable)" in markdown
    assert len(markdown) < 20_000
    assert len(html) < 60_000


def test_companies_list_the_neediest_first(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _settled(storage)
        storage.watchlist_add("targets", "company", "Alpha")
        storage.watchlist_add("targets", "company", "Zeta")
        _followed(storage, "Ann", company="Alpha")
        _followed(storage, "Bo", company="Zeta")
        _followed(storage, "Cy", company="Zeta")
        report = compute_completeness(storage, config)

    markdown = render_completeness_markdown(report)
    assert markdown.index("- Zeta") < markdown.index("- Alpha")


def test_setup_guide_still_counts_everyone_as_tracked(workspace: Path) -> None:
    from wingman.application.setup_guide import _finished_steps

    config = load_config()
    with Storage(config.db_path) as storage:
        _settled(storage)
        _followed(storage, "Fay")
        _import_contact(storage, "Imported A")
        _import_contact(storage, "Imported B")
        report = compute_completeness(storage, config)

    assert "3 people tracked" in _finished_steps(report)
