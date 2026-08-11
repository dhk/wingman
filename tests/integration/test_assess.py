import json
import re
from pathlib import Path

import pytest

from wingman.application.assess import assess_job
from wingman.application.ingest import IngestError
from wingman.application.pov import CORPUS_PERSON_ID, CORPUS_PERSON_NAME
from wingman.domain.profile import ProfileItem
from wingman.domain.values import (
    SCORING_CONTRACT_VERSION,
    AxisDirection,
    ValueAxis,
    ValueAxisEvidence,
    ValueProfile,
    ValueView,
)
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelRequest, ModelResponse

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "fit_assessment" / "case_001_basic"


class TemplatedRecordedProvider:
    """Replays recorded text, substituting __REQ_N__ with requirement IDs from the prompt."""

    def __init__(self, text: str) -> None:
        self._text = text

    def complete(self, request: ModelRequest) -> ModelResponse:
        ids = re.findall(r'"requirement_id": "([^"]+)"', request.prompt)
        text = self._text
        for index, requirement_id in enumerate(ids):
            text = text.replace(f"__REQ_{index}__", requirement_id)
        return ModelResponse(text=text, provider="recorded", model="templated", latency_ms=0)


@pytest.fixture
def workspace(tmp_path: Path) -> Config:
    config = load_config(env={ENV_DATA_DIR: str(tmp_path / "ws")})
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def _seed_profile(storage: Storage) -> None:
    raw = json.loads((FIXTURE / "profile.json").read_text(encoding="utf-8"))
    for entry in raw:
        storage.add_profile_item(ProfileItem.model_validate(entry))


def _providers() -> tuple[TemplatedRecordedProvider, TemplatedRecordedProvider]:
    extract = TemplatedRecordedProvider(
        (FIXTURE / "requirements_response.json").read_text(encoding="utf-8")
    )
    assess = TemplatedRecordedProvider(
        (FIXTURE / "assessment_response.json").read_text(encoding="utf-8")
    )
    return extract, assess


def test_assess_produces_cited_fit_brief(workspace: Config) -> None:
    extract, assess = _providers()
    with Storage(workspace.db_path) as storage:
        _seed_profile(storage)
        report = assess_job(FIXTURE / "job.md", workspace, storage, extract, assess)
        assert storage.count_opportunities() == 1

    assert report.requirements == 4
    assert report.verdicts == {"met": 2, "partial": 1, "unknown": 1}
    assert report.downgraded == []
    assert "Resolve 1 unknown requirement(s)" in report.next_action

    markdown = report.brief_md_path.read_text(encoding="utf-8")
    assert "# Fit Brief: Senior Data Engineer — Acme Analytics" in markdown
    assert "### Met — Apache Kafka in production" in markdown
    assert "> Production experience with Apache Kafka." in markdown
    assert "**Apache Kafka**" in markdown
    assert "Next action:" in markdown

    payload = json.loads(report.brief_json_path.read_text(encoding="utf-8"))
    for assessment in payload["opportunity"]["assessments"]:
        if assessment["verdict"] in {"met", "partial"}:
            assert assessment["evidence_item_ids"], assessment
        else:
            assert assessment["verdict"] in {"gap", "unknown"}, assessment


def test_reassess_updates_existing_opportunity(workspace: Config) -> None:
    extract, assess = _providers()
    with Storage(workspace.db_path) as storage:
        _seed_profile(storage)
        first = assess_job(FIXTURE / "job.md", workspace, storage, extract, assess)
        second = assess_job(FIXTURE / "job.md", workspace, storage, extract, assess)
        assert storage.count_opportunities() == 1
        assert second.opportunity_id == first.opportunity_id
        assert second.source_reused is True


def test_blank_evidence_quote_is_rejected(workspace: Config) -> None:
    extract = TemplatedRecordedProvider(
        '{"requirements": [{"name": "Sneaky", "kind": "required", "quotes": ["  "]},'
        ' {"name": "Python data pipelines", "kind": "required",'
        ' "quotes": ["5+ years building data pipelines in Python."]}]}'
    )
    assess = TemplatedRecordedProvider(
        '{"assessments": [{"requirement_id": "__REQ_0__", "verdict": "gap",'
        ' "rationale": "", "confidence": 0.5}]}'
    )
    with Storage(workspace.db_path) as storage:
        _seed_profile(storage)
        report = assess_job(FIXTURE / "job.md", workspace, storage, extract, assess)
    assert report.requirements == 1
    assert [r.reason for r in report.rejected_requirements] == ["empty evidence quote"]


def test_duplicate_assessment_is_reported_not_silent(workspace: Config) -> None:
    extract, _ = _providers()
    assess = TemplatedRecordedProvider(
        '{"assessments": ['
        '{"requirement_id": "__REQ_0__", "verdict": "gap", "rationale": "first", "confidence": 0.5},'
        '{"requirement_id": "__REQ_0__", "verdict": "met", "rationale": "second",'
        ' "evidence_item_ids": ["item-python"], "confidence": 0.9}]}'
    )
    with Storage(workspace.db_path) as storage:
        _seed_profile(storage)
        report = assess_job(FIXTURE / "job.md", workspace, storage, extract, assess)
        opportunity = storage.find_opportunity_by_source(report.source_record_id)
    assert any("duplicate assessment" in note for note in report.downgraded)
    assert opportunity is not None
    first = next(a for a in opportunity.assessments if a.rationale.startswith("first"))
    assert first.verdict.value == "gap"  # the first assessment wins; the duplicate is dropped


def test_assess_requires_profile(workspace: Config) -> None:
    extract, assess = _providers()
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="wingman ingest"):
            assess_job(FIXTURE / "job.md", workspace, storage, extract, assess)
        assert storage.count_source_records() == 0


# --- the consumer: a fit brief that can cite how you want to work (#356) ------


def _work_profile() -> ValueProfile:
    """A work-view profile of the shape `my_values(view='work')` stores."""
    return ValueProfile(
        subject_id=CORPUS_PERSON_ID,
        subject_name=CORPUS_PERSON_NAME,
        view=ValueView.WORK,
        axes=[
            ValueAxis(
                name="Verification as a precondition for shipping",
                description="Will not ship what nobody can check.",
                score=0.67,
                label="strongly drawn to",
                evidence=[
                    ValueAxisEvidence(
                        item_id="item-verification",
                        subtype="alignment_of_perspective_agree",
                        target="https://example.com/essay",
                        quote="Denying people the information they need is denying them choice.",
                        direction=AxisDirection.SUPPORTS,
                        signed_weight=0.67,
                    )
                ],
            )
        ],
        items_used=7,
        source_item_ids=["item-verification"],
        provider="scripted",
        model="scripted-1",
        prompt_version="value_axes_work_v1",
        scoring_version=SCORING_CONTRACT_VERSION,
    )


def test_the_fit_brief_cites_the_work_profile_when_one_exists(workspace: Config) -> None:
    """The reason to build a second view at all. Without a consumer this is
    a chart nobody reads — so the brief that argues whether a role suits
    somebody names the conditions they need, cited to the same captures the
    axis was scored from."""
    extract, assess = _providers()
    with Storage(workspace.db_path) as storage:
        _seed_profile(storage)
        storage.save_value_profile(_work_profile())
        report = assess_job(FIXTURE / "job.md", workspace, storage, extract, assess)

    markdown = report.brief_md_path.read_text(encoding="utf-8")
    assert "## How you want to work" in markdown
    assert "### Verification as a precondition for shipping (+0.67 — strongly drawn to)" in markdown
    # Cited, not asserted: the capture the axis was read from travels with it.
    assert "Denying people the information they need is denying them choice." in markdown
    assert "`item-verification`" in markdown
    # And it says out loud that it decided nothing above, so nobody reads a
    # working condition as evidence that a requirement is met.
    assert "not evidence that a requirement is met" in markdown

    payload = json.loads(report.brief_json_path.read_text(encoding="utf-8"))
    assert payload["work_profile"]["view"] == "work"


def test_the_work_profile_never_reaches_the_assessing_model(workspace: Config) -> None:
    """An inferred axis must not become the evidence a requirement was
    judged on. With and without a stored work profile, every verdict is
    identical — the brief gains a section, not an opinion."""
    with Storage(workspace.db_path) as storage:
        _seed_profile(storage)
        extract, assess = _providers()
        without = assess_job(FIXTURE / "job.md", workspace, storage, extract, assess)
        # Read now: re-assessing the same posting rewrites the same file,
        # by design (a stable slug per opportunity).
        without_markdown = without.brief_md_path.read_text(encoding="utf-8")

    with Storage(workspace.db_path) as storage:
        storage.save_value_profile(_work_profile())
        extract, assess = _providers()
        with_profile = assess_job(FIXTURE / "job.md", workspace, storage, extract, assess)

    assert with_profile.verdicts == without.verdicts
    assert with_profile.downgraded == without.downgraded
    assert "How you want to work" not in without_markdown


def test_a_brief_carries_the_work_profiles_own_caveats_forward(workspace: Config) -> None:
    """A brief is exactly the document that gets forwarded to somebody who
    will never see the tool that produced it. A caveat left behind at the
    console is a caveat that reached nobody who acts on it."""
    superseded = _work_profile().model_copy(update={"scoring_version": "values-scoring-0"})
    extract, assess = _providers()
    with Storage(workspace.db_path) as storage:
        _seed_profile(storage)
        storage.save_value_profile(superseded)
        report = assess_job(FIXTURE / "job.md", workspace, storage, extract, assess)

    markdown = report.brief_md_path.read_text(encoding="utf-8")
    assert "values-scoring-0" in markdown
    assert "wingman values --refresh --view work" in markdown
