"""Completeness: deterministic counts across the four measurable sections."""

from pathlib import Path

import pytest

from wingman.application.completeness import compute_completeness
from wingman.application.job_scoring import save_criteria
from wingman.application.people import add_person
from wingman.application.relationship import log_interaction
from wingman.domain.profile import ClaimClassification, EvidenceSpan, ProfileItem, ProfileItemKind
from wingman.domain.source_record import SourceRecord
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.reporting.completeness import render_completeness_markdown, write_completeness
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


def test_blocked_sections_named_in_markdown_and_html(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        report = compute_completeness(storage, config)
    markdown = render_completeness_markdown(report)
    assert "Interview Bootstrap" in markdown
    assert "#311" in markdown
    assert "Applications" in markdown
    assert "#312" in markdown
    html = render_completeness_html(report)
    assert "Interview Bootstrap" in html
    assert "<!doctype html>" in html
