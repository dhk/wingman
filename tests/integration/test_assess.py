import json
import re
from pathlib import Path

import pytest

from wingman.application.assess import assess_job
from wingman.application.ingest import IngestError
from wingman.domain.profile import ProfileItem
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
    assessments = payload["opportunity"]["assessments"]
    assert all(
        a["verdict"] in {"met", "partial"}
        and a["evidence_item_ids"]
        or a["verdict"] in {"gap", "unknown"}
        for a in assessments
    )


def test_reassess_updates_existing_opportunity(workspace: Config) -> None:
    extract, assess = _providers()
    with Storage(workspace.db_path) as storage:
        _seed_profile(storage)
        first = assess_job(FIXTURE / "job.md", workspace, storage, extract, assess)
        second = assess_job(FIXTURE / "job.md", workspace, storage, extract, assess)
        assert storage.count_opportunities() == 1
        assert second.opportunity_id == first.opportunity_id
        assert second.source_reused is True


def test_assess_requires_profile(workspace: Config) -> None:
    extract, assess = _providers()
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="wingman ingest"):
            assess_job(FIXTURE / "job.md", workspace, storage, extract, assess)
        assert storage.count_source_records() == 0
