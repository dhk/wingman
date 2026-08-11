"""The company deep-dive, end to end through the CLI (#350).

The point of these tests is the ORDER of events: the spend confirmation
happens before any provider is even constructed, and the storage
confirmation happens before anything reaches the database. Both are proved
by pointing the research capability class at a recorded response file that
does not exist — any code path that actually resolves the provider fails
loudly and visibly in the output.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.config import ENV_DATA_DIR, load_config
from wingman.infrastructure.storage import Storage

runner = CliRunner()

RECORDED_FINDINGS = """{
  "findings": [
    {"dimension": "market_position",
     "claim": "Acme is the second-largest widget vendor in EMEA.",
     "source_url": "https://acme.example/investors/annual-2026",
     "source_title": "Acme 2026 annual report"},
    {"dimension": "values",
     "claim": "Acme says it will delay a launch rather than ship an unsafe product.",
     "quote": "we will delay a launch rather than ship an unsafe product",
     "source_url": "https://acme.example/values",
     "source_title": "Our values"},
    {"dimension": "culture",
     "claim": "Acme invented a four-day week this morning.",
     "source_url": "https://invented.example/four-day-week",
     "source_title": "Nowhere"}
  ]
}

Sources:
- [Acme 2026 annual report](https://acme.example/investors/annual-2026)
- [Our values](https://acme.example/values)
"""

_MISSING_RECORDING = "/nonexistent/wingman-test-never-created.json"


def _point_research_at(config_path: Path, response_path: str) -> None:
    config_path.write_text(
        f'[models.research_websearch]\nprovider = "recorded"\npath = "{response_path}"\n',
        encoding="utf-8",
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "workspace"
    monkeypatch.setenv(ENV_DATA_DIR, str(data_dir))
    runner.invoke(app, ["init"])
    return data_dir


def _stored(name: str = "acme corp") -> object | None:
    config = load_config()
    with Storage(config.db_path) as storage:
        return storage.get_company_dossier(name)


def test_declining_the_spend_prompt_never_reaches_the_provider(workspace: Path) -> None:
    config = load_config()
    _point_research_at(config.models_config_path, _MISSING_RECORDING)

    result = runner.invoke(app, ["company", "deep-dive", "Acme Corp"], input="n\n")

    assert result.exit_code == 1
    assert "Nothing was searched." in result.stdout
    # The confirmation text itself must be informative enough to answer.
    assert "market position" in result.stdout
    assert "$0.04" in result.stdout
    # Proof no paid call was attempted: resolving the provider would have
    # failed on the missing recording and said so.
    assert "could not be read" not in result.stdout
    assert _stored() is None


def test_declining_the_storage_prompt_stores_nothing(workspace: Path, tmp_path: Path) -> None:
    config = load_config()
    recorded = tmp_path / "company-findings.json"
    recorded.write_text(RECORDED_FINDINGS, encoding="utf-8")
    _point_research_at(config.models_config_path, str(recorded))

    result = runner.invoke(app, ["company", "deep-dive", "Acme Corp"], input="y\nn\n")

    assert result.exit_code == 0
    assert "second-largest widget vendor" in result.stdout
    assert "Nothing was stored." in result.stdout
    assert _stored() is None


def test_approving_both_prompts_stores_only_the_verifiably_sourced_findings(
    workspace: Path, tmp_path: Path
) -> None:
    config = load_config()
    recorded = tmp_path / "company-findings.json"
    recorded.write_text(RECORDED_FINDINGS, encoding="utf-8")
    _point_research_at(config.models_config_path, str(recorded))

    result = runner.invoke(app, ["company", "deep-dive", "Acme Corp"], input="y\ny\n")

    assert result.exit_code == 0
    assert "Stored deep-dive for Acme Corp (2 sourced findings)." in result.stdout
    # The third finding cited a page the search never returned: shown as
    # rejected, absent from storage.
    assert "four-day week" in result.stdout
    dossier = _stored()
    assert dossier is not None
    claims = [finding.claim for finding in dossier.findings]  # type: ignore[attr-defined]
    assert "Acme invented a four-day week this morning." not in claims
    assert len(claims) == 2

    shown = runner.invoke(app, ["company", "dossier", "Acme Corp"])
    assert "## Open-web deep dive" in shown.stdout
    assert "https://acme.example/values" in shown.stdout


def test_a_response_with_no_citations_at_all_stores_nothing(
    workspace: Path, tmp_path: Path
) -> None:
    """The whole-response failure mode: confident prose, no retrieval
    evidence. Every finding is refused and the command exits non-zero rather
    than storing an unverifiable dossier."""
    config = load_config()
    recorded = tmp_path / "uncited.json"
    recorded.write_text(RECORDED_FINDINGS.split("\nSources:")[0], encoding="utf-8")
    _point_research_at(config.models_config_path, str(recorded))

    result = runner.invoke(app, ["company", "deep-dive", "Acme Corp"], input="y\ny\n")

    assert result.exit_code == 1
    assert "No finding survived the source gate" in result.output
    assert _stored() is None
