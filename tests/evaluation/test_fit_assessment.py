"""Deterministic evaluation over recorded fit-assessment outputs (runs in CI).

Each case supplies a job description, a seed profile, recorded model outputs
for both steps, and expectations. The invented-evidence canary proves that a
'met' verdict citing a fabricated profile item ID never survives validation.
"""

import json
import re
from pathlib import Path

import pytest

from wingman.application.assess import assess_job
from wingman.domain.profile import ProfileItem
from wingman.infrastructure.config import ENV_DATA_DIR, load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelRequest, ModelResponse

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "fit_assessment"
CASES = sorted(path for path in FIXTURES.iterdir() if path.is_dir())


class TemplatedRecordedProvider:
    def __init__(self, text: str) -> None:
        self._text = text

    def complete(self, request: ModelRequest) -> ModelResponse:
        ids = re.findall(r'"requirement_id": "([^"]+)"', request.prompt)
        text = self._text
        for index, requirement_id in enumerate(ids):
            text = text.replace(f"__REQ_{index}__", requirement_id)
        return ModelResponse(text=text, provider="recorded", model="templated", latency_ms=0)


@pytest.mark.parametrize("case_dir", CASES, ids=lambda p: p.name)
def test_recorded_case(case_dir: Path, tmp_path: Path) -> None:
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    config = load_config(env={ENV_DATA_DIR: str(tmp_path)})
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True, exist_ok=True)
    extract = TemplatedRecordedProvider(
        (case_dir / "requirements_response.json").read_text(encoding="utf-8")
    )
    assess = TemplatedRecordedProvider(
        (case_dir / "assessment_response.json").read_text(encoding="utf-8")
    )

    with Storage(config.db_path) as storage:
        for entry in json.loads((case_dir / "profile.json").read_text(encoding="utf-8")):
            storage.add_profile_item(ProfileItem.model_validate(entry))
        known_item_ids = {item.item_id for item in storage.list_profile_items()}
        report = assess_job(case_dir / "job.md", config, storage, extract, assess)
        opportunity = storage.find_opportunity_by_source(report.source_record_id)

    purpose = expected["purpose"]
    assert report.requirements == expected["expected_requirements"], purpose
    rejected_names = [rejected.name for rejected in report.rejected_requirements]
    assert rejected_names == expected["expected_rejected_requirements"], purpose
    assert report.verdicts == expected["expected_verdicts"], purpose
    assert len(report.downgraded) >= expected["min_adjustments"], purpose

    # Evidence mapping validation: every cited ID resolves to a real profile item,
    # and met/partial verdicts always carry evidence.
    assert opportunity is not None
    job_text = (case_dir / "job.md").read_text(encoding="utf-8")
    for requirement in opportunity.requirements:
        for span in requirement.evidence:
            assert span.quote in job_text, purpose
    for assessment in opportunity.assessments:
        assert set(assessment.evidence_item_ids) <= known_item_ids, purpose
        if assessment.verdict.value in {"met", "partial"}:
            assert assessment.evidence_item_ids, purpose

    markdown = report.brief_md_path.read_text(encoding="utf-8")
    assert "item-kubernetes-invented" not in markdown, purpose
