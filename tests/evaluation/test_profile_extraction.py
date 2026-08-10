"""Deterministic evaluation over recorded model outputs (runs in CI).

Each fixture case supplies a resume, a recorded extraction response, and
expectations. The pipeline's deterministic validation must accept only
evidence-backed items — the fabrication canary must never enter the profile.
"""

import json
import os
from pathlib import Path

import pytest

from wingman.application.evidence import locate_quote
from wingman.application.ingest import ingest_resume
from wingman.application.resume_formats import extract_resume_text
from wingman.infrastructure.config import ENV_DATA_DIR, load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.base import CapabilityClass
from wingman.providers.recorded import RecordedProvider
from wingman.providers.router import get_provider

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "profile_extraction"
CASES = sorted(path for path in FIXTURES.iterdir() if path.is_dir())


@pytest.mark.parametrize("case_dir", CASES, ids=lambda p: p.name)
def test_recorded_case(case_dir: Path, tmp_path: Path) -> None:
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    config = load_config(env={ENV_DATA_DIR: str(tmp_path)})
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True, exist_ok=True)
    provider = RecordedProvider.from_file(case_dir / "response.json")

    with Storage(config.db_path) as storage:
        report = ingest_resume(case_dir / "resume.md", config, storage, provider)
        items = storage.list_profile_items()

    assert report.accepted >= expected["min_accepted"], expected["purpose"]

    accepted_names = {item.name for item in items}
    for name in expected["must_accept_names"]:
        assert name in accepted_names, expected["purpose"]

    rejected_names = {rejected.name for rejected in report.rejected}
    markdown = report.career_md_path.read_text(encoding="utf-8")
    for name in expected["must_reject_names"]:
        assert name in rejected_names, expected["purpose"]
        assert name not in accepted_names
        assert name not in markdown

    # Citation coverage: every accepted item resolves to a real source record,
    # and every quote is verifiable against the text that record covers.
    #
    # Against the EXTRACTED text, not the raw file. The raw file is the
    # archived artifact; the extracted text is what the model read and what
    # content_hash covers, so it is the thing a citation can be checked
    # against. Comparing to the raw file only worked while a fixture happened
    # to quote a hard-wrapped sentence in its wrapped form — the case RFC-026
    # exists because models do NOT do (they quote the sentence, not the line
    # breaks), and the case #278's PDFs break outright.
    extracted = extract_resume_text(case_dir / "resume.md")
    for item in items:
        for span in item.evidence:
            assert span.source_record_id == report.source_record_id
            # Resolvable, not literally-a-substring: a stored quote folds the
            # whitespace runs that PDF layout extraction pads with (#278).
            assert locate_quote(span.quote, extracted) is not None


@pytest.mark.evaluation_live
def test_live_extraction_smoke(tmp_path: Path) -> None:
    """Calls the live extract_fast model. Run with: pytest -m evaluation_live."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    config = load_config(env={ENV_DATA_DIR: str(tmp_path)})
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True, exist_ok=True)
    from wingman.providers.router import DEFAULT_MODELS_TOML

    config.models_config_path.write_text(DEFAULT_MODELS_TOML, encoding="utf-8")
    provider = get_provider(CapabilityClass.EXTRACT_FAST, config)
    with Storage(config.db_path) as storage:
        report = ingest_resume(FIXTURES / "case_001_basic" / "resume.md", config, storage, provider)
    assert report.accepted >= 1
