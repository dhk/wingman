"""Completeness: deterministic counts across the four measurable sections."""

from pathlib import Path

import pytest

from wingman.application.completeness import compute_completeness, next_actions
from wingman.application.job_scoring import save_criteria
from wingman.application.people import add_person, attach_feed
from wingman.application.relationship import log_interaction
from wingman.domain.person import FeedSource, Person, PersonOrigin
from wingman.domain.profile import ClaimClassification, EvidenceSpan, ProfileItem, ProfileItemKind
from wingman.domain.relationship import RelationshipObjective
from wingman.domain.source_record import SourceRecord
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.reporting.completeness import (
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


def test_companies_derived_from_watched_people(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Jack Beckwith", storage, company="The DataFace")
        add_person("Michael Hester", storage, company="The DataFace")
        add_person("No Company Person", storage)
        report = compute_completeness(storage, config)
    assert len(report.companies) == 1
    company = report.companies[0]
    assert company.name == "The DataFace"
    assert company.people_watched == 2
    assert company.pov_cards == 0
    assert company.missing_pov_cards == 2


def test_companies_dedup_by_key_not_raw_string(workspace: Path) -> None:
    """'Acme' and 'ACME' normalize to the same company_key and must land in
    one row, not two duplicate, double-counted ones (review comment on #313)."""
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Alice", storage, company="Acme")
        add_person("Bob", storage, company="ACME")
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


def test_imported_contacts_are_not_completeness_gaps(workspace: Path) -> None:
    """#465/#545: a connections import put 2,600+ people in the store and the
    whole 'Things to do' list became 'build POV cards for 2628 watched people'
    and 'log what happened with 2669 people' — walls, not actions."""
    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Curated Carla", storage, company="Acme")
        for index in range(25):
            _import_contact(storage, f"Imported {index}", company="Initech")
        report = compute_completeness(storage, config)
        titles = [action.title for action in next_actions(report)]

    assert [person.name for person in report.people] == ["Curated Carla"]
    assert report.imported_people == 25
    assert "Build POV cards for 1 watched people" in titles
    assert "Log what happened with 1 people" in titles
    # The import's company must not create a company row either: Initech has
    # no curated person.
    assert [company.name for company in report.companies] == ["Acme"]


def test_a_feed_or_a_log_entry_brings_an_import_into_the_list(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        fed = _import_contact(storage, "Fed Fiona", company="Acme")
        _import_contact(storage, "Logged Lou", company="Acme")
        _import_contact(storage, "Untouched Una", company="Acme")
        attach_feed(fed, FeedSource(url="https://fiona.example/feed"), storage)
        log_interaction("Logged Lou", "Coffee.", config, storage)
        report = compute_completeness(storage, config)

    assert {person.name for person in report.people} == {"Fed Fiona", "Logged Lou"}
    assert report.imported_people == 1
    assert report.companies[0].people_watched == 2


def test_an_objective_or_a_watchlist_entry_curates_an_import(workspace: Path) -> None:
    """Saving a goal/thesis/next move for someone, or putting them on a
    watchlist, is as deliberate as logging with them. Found by probing the
    first version of this change, which left both in the 'untouched' count."""
    config = load_config()
    with Storage(config.db_path) as storage:
        with_goal = _import_contact(storage, "Objective Olive", company="Acme")
        storage.save_objective(
            RelationshipObjective(
                person_id=with_goal.person_id, goal="g", thesis="t", next_move="n"
            )
        )
        _import_contact(storage, "Watched  Wes", company="Acme")
        storage.watchlist_add("overnight", "person", "watched wes")
        # A company on a watchlist is not a person on it.
        _import_contact(storage, "Acme", company="Acme")
        storage.watchlist_add("overnight", "company", "Acme")
        _import_contact(storage, "Untouched Una", company="Acme")
        report = compute_completeness(storage, config)

    assert {person.name for person in report.people} == {"Objective Olive", "Watched  Wes"}
    assert report.imported_people == 2


def test_editing_an_import_with_people_add_does_not_curate_it(workspace: Path) -> None:
    """Documents a known limit rather than endorsing it: add_person keeps
    origin=linkedin_connections and there is no stored 'touched' signal. If
    this test starts failing because someone added one, update it and the
    _split_people docstring together."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _import_contact(storage, "Edited Ed", company="Acme")
        add_person("Edited Ed", storage, position="CTO")
        report = compute_completeness(storage, config)

    assert report.people == []
    assert report.imported_people == 1


def test_the_people_heading_does_not_claim_zero_tracked_over_an_import(
    workspace: Path,
) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        for index in range(30):
            _import_contact(storage, f"Imported {index}")
        report = compute_completeness(storage, config)

    markdown = render_completeness_markdown(report)
    assert "## People (0 curated)" in markdown
    assert "tracked)" not in markdown
    assert "People (0 curated)" in render_completeness_html(report)
    assert "30 imported contacts" in markdown


def test_the_report_stays_bounded_however_many_people_are_imported(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        for index in range(2000):
            _import_contact(storage, f"Contact {index}", company=f"Company {index % 400}")
        report = compute_completeness(storage, config)

    markdown = render_completeness_markdown(report)
    html = render_completeness_html(report)
    assert len(markdown) < 8_000
    assert len(html) < 40_000
    assert "2000 imported contacts not listed or counted as gaps" in markdown
    assert "Contact 17" not in markdown
    assert report.companies == []


def test_curated_people_are_capped_in_the_rendered_report(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        for index in range(MAX_PEOPLE_LISTED + 7):
            add_person(f"Person {index:03d}", storage)
        report = compute_completeness(storage, config)

    assert len(report.people) == MAX_PEOPLE_LISTED + 7
    assert "…and 7 more" in render_completeness_markdown(report)
    assert "…and 7 more" in render_completeness_html(report)


def test_setup_guide_still_counts_imported_people_as_tracked(workspace: Path) -> None:
    from wingman.application.setup_guide import _finished_steps

    config = load_config()
    with Storage(config.db_path) as storage:
        add_person("Curated Carla", storage)
        _import_contact(storage, "Imported A")
        _import_contact(storage, "Imported B")
        report = compute_completeness(storage, config)

    assert "3 people tracked" in _finished_steps(report)
