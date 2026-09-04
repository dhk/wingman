"""Opportunity summaries (#312): one row per assessed opportunity, best-effort extras."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wingman.application.answers import save_answer
from wingman.application.opportunities import list_opportunity_summaries, render_opportunity_listing
from wingman.application.pack import _slug, build_application_pack
from wingman.application.people import add_person
from wingman.application.similarity import infer_company_from_title
from wingman.domain.opportunity import (
    FitVerdict,
    Opportunity,
    Requirement,
    RequirementAssessment,
    RequirementKind,
    count_verdicts,
)
from wingman.domain.profile import EvidenceSpan
from wingman.infrastructure.config import Config, load_config
from wingman.infrastructure.storage import Storage


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def _requirement(name: str, quote: str) -> Requirement:
    return Requirement(
        name=name,
        kind=RequirementKind.REQUIRED,
        evidence=[EvidenceSpan(source_record_id="r", quote=quote)],
    )


def _seed(
    storage: Storage,
    title: str,
    source_record_id: str,
    created_at: datetime,
    verdicts: list[FitVerdict],
) -> Opportunity:
    """One opportunity with one requirement per verdict given, in order."""
    requirements = [_requirement(f"{title}-req-{i}", "asks for it") for i in range(len(verdicts))]
    assessments = [
        RequirementAssessment(
            requirement_id=requirement.requirement_id,
            verdict=verdict,
            rationale="because",
            confidence=0.5,
        )
        for requirement, verdict in zip(requirements, verdicts, strict=True)
    ]
    opportunity = Opportunity(
        title=title,
        source_record_id=source_record_id,
        next_action=f"Decide on {title}",
        requirements=requirements,
        assessments=assessments,
        created_at=created_at,
    )
    storage.save_opportunity(opportunity)
    return opportunity


def test_three_opportunities_return_three_named_entries(workspace: Config) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with Storage(workspace.db_path) as storage:
        first = _seed(storage, "Staff MLE at Acme", "r1", base, [FitVerdict.MET])
        second = _seed(
            storage, "Head of Data at Woven", "r2", base + timedelta(days=1), [FitVerdict.GAP]
        )
        third = _seed(
            storage,
            "Platform Lead at Globex",
            "r3",
            base + timedelta(days=2),
            [FitVerdict.UNKNOWN],
        )
        summaries = list_opportunity_summaries(storage, workspace)

    assert len(summaries) == 3
    # storage.list_opportunities' ORDER BY created_at is preserved, unreordered.
    assert [s.title for s in summaries] == [first.title, second.title, third.title]
    assert [s.opportunity_id for s in summaries] == [
        first.opportunity_id,
        second.opportunity_id,
        third.opportunity_id,
    ]
    for summary, opportunity in zip(summaries, (first, second, third), strict=True):
        assert summary.title == opportunity.title
        assert summary.next_action == opportunity.next_action
        assert summary.assessed_at == opportunity.created_at


def test_company_inference_matches_packs_existing_behavior(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        add_person("Jane Author", storage, company="Acme", position="Head of Data")
        opportunity = _seed(
            storage, "Staff MLE at Acme", "r1", datetime(2026, 1, 1, tzinfo=UTC), [FitVerdict.MET]
        )
        pack_report = build_application_pack("staff mle", workspace, storage)
        summaries = list_opportunity_summaries(storage, workspace)

    assert pack_report.company == "Acme"
    assert summaries[0].opportunity_id == opportunity.opportunity_id
    # Same underlying inference (application.similarity.infer_company_from_title)
    # backs both call sites, so they must agree.
    assert summaries[0].company == pack_report.company == "Acme"


def test_verdict_counts_match_fit_briefs_existing_behavior(workspace: Config) -> None:
    verdicts = [
        FitVerdict.MET,
        FitVerdict.MET,
        FitVerdict.PARTIAL,
        FitVerdict.GAP,
        FitVerdict.UNKNOWN,
    ]
    with Storage(workspace.db_path) as storage:
        opportunity = _seed(
            storage, "Staff MLE at Acme", "r1", datetime(2026, 1, 1, tzinfo=UTC), verdicts
        )
        summaries = list_opportunity_summaries(storage, workspace)

    expected = {
        verdict.value: count for verdict, count in count_verdicts(opportunity.assessments).items()
    }
    assert summaries[0].verdict_counts == expected
    assert summaries[0].verdict_counts == {"met": 2, "partial": 1, "gap": 1, "unknown": 1}


def test_pack_composed_and_answers_matched_are_best_effort_and_documented(
    workspace: Config,
) -> None:
    with Storage(workspace.db_path) as storage:
        add_person("Jane Author", storage, company="Acme", position="Head of Data")
        _seed(
            storage,
            "Staff MLE at Acme",
            "r1",
            datetime(2026, 1, 1, tzinfo=UTC),
            [FitVerdict.MET],
        )
        # No pack composed yet, no matching answers yet.
        [before] = list_opportunity_summaries(storage, workspace)
        assert before.pack_composed is False
        assert before.answers_matched == 0

        build_application_pack("staff mle", workspace, storage)
        save_answer(
            "Why Acme?",
            "Because of the mission.",
            storage,
            company="Acme",
            role_title="Staff MLE",
        )
        [after] = list_opportunity_summaries(storage, workspace)

    assert after.pack_composed is True
    packs_dir = workspace.reports_dir / "packs"
    assert any(packs_dir.glob(f"pack-{_slug('Staff MLE at Acme')}-*.md"))
    assert after.answers_matched == 1

    # The approximation must be visible to callers, not silently presented
    # as fact (AGENTS.md #9, partial truth over polished fiction).
    module_doc = __import__(
        "wingman.application.opportunities", fromlist=["list_opportunity_summaries"]
    ).__doc__
    assert "best-effort" in module_doc.lower()
    assert "foreign key" in module_doc and "opportunity_id" in module_doc

    listing = render_opportunity_listing([after])
    assert "best-effort" in listing
    assert "approx" in listing


def test_render_opportunity_listing_shape(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        _seed(
            storage, "Staff MLE at Acme", "r1", datetime(2026, 1, 1, tzinfo=UTC), [FitVerdict.MET]
        )
        _seed(
            storage,
            "Head of Data at Woven",
            "r2",
            datetime(2026, 1, 2, tzinfo=UTC),
            [FitVerdict.GAP],
        )
        summaries = list_opportunity_summaries(storage, workspace)

    listing = render_opportunity_listing(summaries)
    lines = listing.splitlines()
    assert len(lines) == 3  # one line per opportunity + a total count line
    assert lines[0].startswith("Staff MLE at Acme")
    assert lines[1].startswith("Head of Data at Woven")
    assert lines[-1] == "2 opportunities."


def test_render_opportunity_listing_empty(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        summaries = list_opportunity_summaries(storage, workspace)
    assert summaries == []
    assert "No assessed opportunities" in render_opportunity_listing(summaries)


def test_infer_company_from_title_used_directly_matches_pack(workspace: Config) -> None:
    """Sanity check that the promoted helper behaves exactly like the old
    private pack._infer_company did (#312)."""
    with Storage(workspace.db_path) as storage:
        add_person("Jane Author", storage, company="Acme", position="Head of Data")
        assert infer_company_from_title("Staff MLE at Acme", storage) == "Acme"
        assert infer_company_from_title("Staff MLE at Nowhere Co", storage) is None


def test_a_placeholder_company_no_longer_matches_inside_a_word(workspace: Config) -> None:
    """LinkedIn writes "NA" into the company column of a connection with no
    employer, so an imported person arrives carrying it as a real company.
    company_key("NA") is "na", which is a substring of "data-a[na]lyst" —
    the bare `in` test attributed the role to a company that does not exist
    and then listed everyone else carrying the placeholder as colleagues.
    """
    with Storage(workspace.db_path) as storage:
        add_person("Rob Placeholder", storage, company="NA", position="Retired")
        title = "Source: https://cursor.com/careers/data-analyst-user-operations"
        assert infer_company_from_title(title, storage) is None
        for word in ("Manager, Analytics", "International Sales", "National Accounts Lead"):
            assert infer_company_from_title(word, storage) is None


def test_short_company_names_still_match_as_whole_words(workspace: Config) -> None:
    """The fix is a boundary check, not a length floor: two-character
    company names are real and must keep working."""
    with Storage(workspace.db_path) as storage:
        add_person("Pat Printer", storage, company="HP", position="Engineer")
        assert infer_company_from_title("Data Analyst at HP", storage) == "HP"
        assert infer_company_from_title("HP, Staff Engineer", storage) == "HP"
        # ...but not buried inside another word.
        assert infer_company_from_title("Head of HPC Strategy", storage) is None


def test_the_longest_matching_company_still_wins(workspace: Config) -> None:
    """Boundary matching must not disturb the existing specific-beats-generic
    rule that makes Anthropic roles resolve correctly today."""
    with Storage(workspace.db_path) as storage:
        add_person("Ann Broad", storage, company="AI", position="Founder")
        add_person("Ann Narrow", storage, company="AI Deployment", position="Lead")
        assert infer_company_from_title("Manager at AI Deployment", storage) == "AI Deployment"


def test_a_company_name_with_punctuation_still_anchors(workspace: Config) -> None:
    """Lookarounds rather than \\b, so a key bordered by punctuation matches."""
    with Storage(workspace.db_path) as storage:
        add_person("Sam Assoc", storage, company="iO Associates - US", position="Recruiter")
        assert (
            infer_company_from_title("Analyst, iO Associates - US", storage) == "iO Associates - US"
        )


def test_a_punctuation_only_company_is_never_matched(workspace: Config) -> None:
    """The same import that yields "NA" also yields "-", "." and "..". A bare
    "-" passes a whole-word check inside "Regional Director - AI Deployment",
    so the boundary rule alone is not enough: a key with no alphanumeric
    character in it is a placeholder and never a name."""
    with Storage(workspace.db_path) as storage:
        add_person("Dash Person", storage, company="-", position="Unknown")
        add_person("Dot Person", storage, company="..", position="Unknown")
        assert infer_company_from_title("Regional Director - AI Deployment", storage) is None
        assert infer_company_from_title("Head of Data .. Strategy", storage) is None
