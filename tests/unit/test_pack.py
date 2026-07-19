"""Posting fetch + application pack (RFC-024): signal to application, deterministically."""

from pathlib import Path

import pytest

from wingman.application.assess import fetch_job_posting
from wingman.application.ingest import IngestError
from wingman.application.pack import build_application_pack
from wingman.application.people import add_person
from wingman.domain.opportunity import (
    FitVerdict,
    Opportunity,
    Requirement,
    RequirementAssessment,
    RequirementKind,
)
from wingman.domain.pov import PovCard, Stance, StanceDimension
from wingman.domain.profile import (
    ClaimClassification,
    EvidenceSpan,
    ProfileItem,
    ProfileItemKind,
)
from wingman.infrastructure.config import load_config
from wingman.infrastructure.fetch import FetchError
from wingman.infrastructure.storage import Storage

POSTING = (
    b"<html><head><title>Staff MLE</title><script>junk()</script></head><body>"
    b"<h1>Staff MLE at Acme</h1><p>" + b"We need someone who has shipped Kafka pipelines "
    b"at scale and can reason about semantic layers in production systems. "
    * 5
    + b"</p></body></html>"
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return tmp_path


def test_fetch_job_posting_archives_visible_text(workspace: Path) -> None:
    config = load_config()
    path = fetch_job_posting("https://acme.example.com/jobs/mle", config, fetcher=lambda u: POSTING)
    assert path.parent == config.inbox_dir
    text = path.read_text(encoding="utf-8")
    assert text.startswith("Source: https://acme.example.com/jobs/mle")
    assert "Kafka pipelines" in text and "junk()" not in text


def test_fetch_job_posting_fails_visibly(workspace: Path) -> None:
    config = load_config()
    with pytest.raises(IngestError, match="https"):
        fetch_job_posting("http://acme.example.com/jobs", config, fetcher=lambda u: POSTING)
    with pytest.raises(IngestError, match="readable text"):
        fetch_job_posting(
            "https://acme.example.com/jobs",
            config,
            fetcher=lambda u: b"<html><body>Log in</body></html>",
        )

    def boom(url: str) -> bytes:
        raise FetchError("HTTP Error 403")

    with pytest.raises(IngestError, match="could not fetch"):
        fetch_job_posting("https://acme.example.com/jobs", config, fetcher=boom)


def _seed_opportunity(storage: Storage) -> None:
    item = ProfileItem(
        kind=ProfileItemKind.ACHIEVEMENT,
        name="Kafka migration",
        classification=ClaimClassification.FACT,
        confidence=0.9,
        evidence=[EvidenceSpan(source_record_id="r1", quote="We moved billing to Apache Kafka.")],
        prompt_version="v1",
        extracted_by="test",
    )
    storage.add_profile_item(item)
    req_met = Requirement(
        name="Streaming pipelines",
        kind=RequirementKind.REQUIRED,
        evidence=[EvidenceSpan(source_record_id="r2", quote="shipped Kafka pipelines")],
    )
    req_gap = Requirement(
        name="Rust",
        kind=RequirementKind.PREFERRED,
        evidence=[EvidenceSpan(source_record_id="r2", quote="Rust experience")],
    )
    storage.save_opportunity(
        Opportunity(
            title="Staff MLE at Acme",
            source_record_id="r2",
            next_action="Decide whether to pursue",
            requirements=[req_met, req_gap],
            assessments=[
                RequirementAssessment(
                    requirement_id=req_met.requirement_id,
                    verdict=FitVerdict.MET,
                    rationale="Direct experience.",
                    evidence_item_ids=[item.item_id],
                    confidence=0.9,
                ),
                RequirementAssessment(
                    requirement_id=req_gap.requirement_id,
                    verdict=FitVerdict.GAP,
                    rationale="No evidence.",
                    confidence=0.8,
                ),
            ],
        )
    )


def test_pack_composes_fit_fodder_and_company(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_opportunity(storage)
        add_person("Jane Author", storage, company="Acme", position="Head of Data")
        storage.save_pov_card(
            PovCard(
                person_id="__company__acme",
                person_name="Acme (company)",
                stances=[
                    Stance(
                        statement="Acme ships small.",
                        quote="We ship small changes every day.",
                        doc_id="d1",
                        doc_title="Ship Small — by Acme",
                        source_record_id="r3",
                        dimension=StanceDimension.ATTITUDE,
                    )
                ],
                topics=["shipping"],
                documents_used=1,
                provider="scripted",
                model="scripted-1",
                prompt_version="v2",
            )
        )
        report = build_application_pack("staff mle", config, storage)
    assert report.company == "Acme"
    text = report.markdown
    assert "# Application pack: Staff MLE at Acme" in text
    assert "✓ **Streaming pipelines** (met)" in text and "✗ **Rust** (gap)" in text
    assert "gap: 1  met: 1" in text
    # fodder quotes the user's own evidence verbatim, only for met/partial
    assert '**Streaming pipelines** → Kafka migration: "We moved billing to Apache Kafka."' in text
    assert "Rust" not in text.split("Cover-letter fodder")[1].split("##")[0]
    # company intelligence rides along
    assert "[inference] [attitude] Acme ships small." in text
    assert "Jane Author — Head of Data" in text
    assert Path(report.path).exists() and "packs" in report.path
    assert "never sends" in text


def test_pack_errors_are_actionable(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="no assessed opportunities"):
            build_application_pack("anything", config, storage)
        _seed_opportunity(storage)
        with pytest.raises(IngestError, match="Recent: Staff MLE at Acme"):
            build_application_pack("cfo role", config, storage)
        # unknown company: pack still builds, says how to attach intelligence
        report = build_application_pack("staff mle", config, storage, company="Nowhere Co")
        assert "no synthesized themes yet" in report.markdown
