from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from wingman.domain import ClaimClassification, Provenance, SourceRecord, TransformationStep


def _provenance(**overrides: object) -> Provenance:
    defaults: dict[str, object] = {
        "source_type": "resume",
        "source_locator": "inbox/resume.md",
        "classification": ClaimClassification.FACT,
    }
    defaults.update(overrides)
    return Provenance.model_validate(defaults)


def test_provenance_defaults() -> None:
    provenance = _provenance()
    assert provenance.record_id
    assert provenance.ingested_at.tzinfo is not None
    assert provenance.transformations == []
    assert provenance.user_overrides == []
    assert provenance.confidence == 1.0


def test_provenance_ids_are_unique() -> None:
    assert _provenance().record_id != _provenance().record_id


def test_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        _provenance(confidence=1.5)
    with pytest.raises(ValidationError):
        _provenance(confidence=-0.1)


def test_transformation_history_appends() -> None:
    step = TransformationStep(description="extracted skills", performed_by="extract_fast")
    provenance = _provenance(transformations=[step])
    assert provenance.transformations[0].description == "extracted skills"
    assert provenance.transformations[0].performed_at.tzinfo is not None


def test_source_record_is_immutable() -> None:
    record = SourceRecord(
        source_type="resume",
        source_locator="inbox/resume.md",
        content_hash="abc123",
        source_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(ValidationError):
        record.content_hash = "tampered"  # type: ignore[misc]
