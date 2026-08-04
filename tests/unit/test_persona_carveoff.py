"""Persona carve-off, Phase 1 of #235 (docs/RFC.md RFC-049):
export a coached persona's captured interview data and seed it as a
brand-new Wingman workspace's own first-person profile."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.application.coaching import find_or_create_persona
from wingman.application.ingest import IngestError
from wingman.application.interview import capture_interview_reaction
from wingman.application.persona_carveoff import (
    PLACEHOLDER_SOURCE_TYPE,
    carve_off_persona,
    export_persona,
    render_carveoff_report,
    seed_new_workspace,
)
from wingman.domain.profile import ItemStatus
from wingman.domain.provenance import ClaimClassification
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.reporting.career import render_career

runner = CliRunner()

WHY_PRO = "She spent decades building trust with a species that can't reciprocate in words."
WHY_CON = "The company treats every user as inventory to be optimized against."


@pytest.fixture
def coach_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "coach"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    Storage(config.db_path).close()
    return config


def _seed_persona_capture(config: Config, persona_name: str = "Mike Chen") -> str:
    """Create a persona with one coach-speculated capture; return persona_id."""
    with Storage(config.db_path) as storage:
        persona = find_or_create_persona(persona_name, storage)
        capture_interview_reaction(
            "values_pro", "Jane Goodall", WHY_PRO, config, storage, persona_id=persona.persona_id
        )
        return persona.persona_id


# --- export_persona -------------------------------------------------------


def test_export_persona_gathers_only_that_personas_active_items(coach_workspace: Config) -> None:
    with Storage(coach_workspace.db_path) as storage:
        mike = find_or_create_persona("Mike Chen", storage)
        priya = find_or_create_persona("Priya Nair", storage)
        capture_interview_reaction(
            "values_pro",
            "Jane Goodall",
            WHY_PRO,
            coach_workspace,
            storage,
            persona_id=mike.persona_id,
        )
        capture_interview_reaction(
            "values_con",
            "Acme Corp",
            WHY_CON,
            coach_workspace,
            storage,
            persona_id=priya.persona_id,
        )
        # the coach's own (unscoped) work must never leak into the export either
        capture_interview_reaction("values_pro", "Jane Goodall", WHY_PRO, coach_workspace, storage)

        export = export_persona("Mike Chen", storage)

    assert export.persona_name == "Mike Chen"
    assert len(export.items) == 1
    item = export.items[0]
    assert item.persona_id is None  # rehomed as first-person
    assert item.name == "values_pro: Jane Goodall"  # persona suffix stripped
    assert item.detail == WHY_PRO
    assert item.classification is ClaimClassification.HYPOTHESIS  # provenance preserved
    assert item.extracted_by == "coach"
    assert len(export.source_records) == 1
    assert export.source_records[0].source_type == PLACEHOLDER_SOURCE_TYPE


def test_export_persona_excludes_superseded_and_conflict_items(coach_workspace: Config) -> None:
    with Storage(coach_workspace.db_path) as storage:
        mike = find_or_create_persona("Mike Chen", storage)
        capture_interview_reaction(
            "values_pro",
            "Jane Goodall",
            WHY_PRO,
            coach_workspace,
            storage,
            persona_id=mike.persona_id,
        )
        # a second, different capture for the same subtype+target is a genuine
        # cross-source rival under RFC-028 only if evidence differs; here we craft
        # an explicit conflict row directly to prove it's excluded regardless.
        item = storage.list_profile_items()[0]
        rival = item.model_copy(
            update={
                "item_id": "rival-item",
                "status": ItemStatus.CONFLICT,
                "conflicts_with": item.item_id,
                "detail": "a rival, unresolved claim",
            }
        )
        storage.add_profile_item(rival)

        export = export_persona("Mike Chen", storage)

    assert len(export.items) == 1
    assert export.items[0].detail == WHY_PRO


def test_export_persona_unknown_name_fails(coach_workspace: Config) -> None:
    with Storage(coach_workspace.db_path) as storage:
        with pytest.raises(IngestError, match="no persona"):
            export_persona("Nobody", storage)


def test_export_persona_with_no_captures_fails(coach_workspace: Config) -> None:
    with Storage(coach_workspace.db_path) as storage:
        find_or_create_persona("Mike Chen", storage)
        with pytest.raises(IngestError, match="nothing to carve off"):
            export_persona("Mike Chen", storage)


def test_export_persona_resolves_by_id_too(coach_workspace: Config) -> None:
    persona_id = _seed_persona_capture(coach_workspace)
    with Storage(coach_workspace.db_path) as storage:
        export = export_persona(persona_id, storage)
    assert export.persona_name == "Mike Chen"


# --- seed_new_workspace / evidence integrity -------------------------------


def test_seed_new_workspace_writes_items_and_placeholder_records(
    coach_workspace: Config, tmp_path: Path
) -> None:
    with Storage(coach_workspace.db_path) as storage:
        mike = find_or_create_persona("Mike Chen", storage)
        capture_interview_reaction(
            "values_pro",
            "Jane Goodall",
            WHY_PRO,
            coach_workspace,
            storage,
            persona_id=mike.persona_id,
        )
        export = export_persona("Mike Chen", storage)

    target_dir = tmp_path / "mike-workspace"
    target_dir.mkdir(parents=True)
    target_config = Config(data_dir=target_dir, data_dir_source="test")
    with Storage(target_config.db_path) as target_storage:
        counts = seed_new_workspace(export, target_storage)
        assert counts.accepted == 1

        items = target_storage.list_profile_items()
        assert len(items) == 1
        seeded = items[0]
        assert seeded.persona_id is None
        assert seeded.detail == WHY_PRO

        # evidence integrity (RFC-049): the referenced source record resolves,
        # honestly, in the NEW workspace — never a dangling reference.
        record = target_storage.get_source_record(seeded.evidence[0].source_record_id)
        assert record is not None
        assert record.source_type == PLACEHOLDER_SOURCE_TYPE
        assert "carved off from a coach's workspace" in record.source_locator.lower()
        assert "Mike Chen" in record.source_locator


def test_seed_new_workspace_refuses_non_empty_target(
    coach_workspace: Config, tmp_path: Path
) -> None:
    with Storage(coach_workspace.db_path) as storage:
        mike = find_or_create_persona("Mike Chen", storage)
        capture_interview_reaction(
            "values_pro",
            "Jane Goodall",
            WHY_PRO,
            coach_workspace,
            storage,
            persona_id=mike.persona_id,
        )
        export = export_persona("Mike Chen", storage)

    target_dir = tmp_path / "populated"
    target_dir.mkdir(parents=True)
    target_config = Config(data_dir=target_dir, data_dir_source="test")
    with Storage(target_config.db_path) as target_storage:
        from wingman.domain.profile import EvidenceSpan, ProfileItem, ProfileItemKind

        pre_existing = ProfileItem(
            kind=ProfileItemKind.SKILL,
            name="Python",
            classification=ClaimClassification.FACT,
            confidence=1.0,
            evidence=[EvidenceSpan(source_record_id="doesnt-matter", quote="q")],
            prompt_version="v0",
            extracted_by="user",
        )
        target_storage.add_profile_item(pre_existing)

        with pytest.raises(IngestError, match="Phase 2"):
            seed_new_workspace(export, target_storage)

        # refusal is a fail-loud no-op: nothing new was written
        assert target_storage.count_profile_items() == 1


def test_carveoff_evidence_is_citable_via_career_render(
    coach_workspace: Config, tmp_path: Path
) -> None:
    """Regression coverage for RFC-049: a carved-off item whose evidence
    resolves cleanly through this codebase's own citation surface
    (reporting/career.py), not just via a raw storage lookup."""
    from wingman.domain.profile import EvidenceSpan, ProfileItem, ProfileItemKind

    with Storage(coach_workspace.db_path) as storage:
        mike = find_or_create_persona("Mike Chen", storage)
        # career.md only renders achievement/skill/role/testimonial kinds —
        # construct a persona-scoped one directly to exercise that path too,
        # since interview captures alone never reach it.
        item = ProfileItem(
            kind=ProfileItemKind.SKILL,
            persona_id=mike.persona_id,
            name="Grant writing",
            detail="Landed three consecutive multi-year foundation grants.",
            classification=ClaimClassification.HYPOTHESIS,
            confidence=0.6,
            evidence=[EvidenceSpan(source_record_id="coach-record-1", quote="grant history")],
            prompt_version="v0",
            extracted_by="coach",
        )
        storage.add_profile_item(item)
        export = export_persona("Mike Chen", storage)

    target_dir = tmp_path / "mike-workspace"
    target_dir.mkdir(parents=True)
    target_config = Config(data_dir=target_dir, data_dir_source="test")
    (target_config.reports_dir).mkdir(parents=True)
    with Storage(target_config.db_path) as target_storage:
        seed_new_workspace(export, target_storage)
        _json, md_path = render_career(
            target_storage,
            target_config,
            run_meta={"provider": "test", "model": "-", "prompt_version": "-", "generated_at": "-"},
        )
        rendered = md_path.read_text(encoding="utf-8")
        assert "Grant writing" in rendered
        assert "## Evidence" in rendered
        assert "grant history" in rendered
        seeded = [i for i in target_storage.list_profile_items() if i.name == "Grant writing"][0]
        record_id = seeded.evidence[0].source_record_id
        assert f"source record `{record_id}`" in rendered
        # and the citation actually resolves, not just prints a bare id
        assert target_storage.get_source_record(record_id) is not None


# --- carve_off_persona (end-to-end orchestration) --------------------------


def test_carve_off_persona_end_to_end(coach_workspace: Config, tmp_path: Path) -> None:
    with Storage(coach_workspace.db_path) as storage:
        mike = find_or_create_persona("Mike Chen", storage)
        capture_interview_reaction(
            "values_pro",
            "Jane Goodall",
            WHY_PRO,
            coach_workspace,
            storage,
            persona_id=mike.persona_id,
        )

    target_dir = tmp_path / "brand-new"
    with Storage(coach_workspace.db_path) as storage:
        report = carve_off_persona("Mike Chen", coach_workspace, storage, target_dir)

    assert report.persona_name == "Mike Chen"
    assert report.counts.accepted == 1
    assert report.source_records_written == 1
    summary = render_carveoff_report(report)
    assert "Mike Chen" in summary and "Phase 1" in summary and "Phase 2" in summary
    assert target_dir.exists()

    with Storage(target_dir / "wingman.db") as target_storage:
        items = target_storage.list_profile_items()
        assert len(items) == 1
        assert items[0].persona_id is None


def test_carve_off_persona_refuses_same_workspace_as_target(coach_workspace: Config) -> None:
    _seed_persona_capture(coach_workspace)
    with Storage(coach_workspace.db_path) as storage:
        with pytest.raises(IngestError, match="different workspace"):
            carve_off_persona("Mike Chen", coach_workspace, storage, coach_workspace.data_dir)


def test_carve_off_persona_refuses_populated_target(
    coach_workspace: Config, tmp_path: Path
) -> None:
    _seed_persona_capture(coach_workspace)
    target_dir = tmp_path / "already-used"
    target_dir.mkdir(parents=True)
    target_config = Config(data_dir=target_dir, data_dir_source="test")
    with Storage(target_config.db_path) as target_storage:
        from wingman.domain.profile import EvidenceSpan, ProfileItem, ProfileItemKind

        target_storage.add_profile_item(
            ProfileItem(
                kind=ProfileItemKind.SKILL,
                name="Something",
                classification=ClaimClassification.FACT,
                confidence=1.0,
                evidence=[EvidenceSpan(source_record_id="x", quote="q")],
                prompt_version="v0",
                extracted_by="user",
            )
        )

    with Storage(coach_workspace.db_path) as storage:
        with pytest.raises(IngestError, match="Phase 2"):
            carve_off_persona("Mike Chen", coach_workspace, storage, target_dir)


def test_carve_off_persona_creates_missing_target_directory(
    coach_workspace: Config, tmp_path: Path
) -> None:
    _seed_persona_capture(coach_workspace)
    target_dir = tmp_path / "does" / "not" / "exist" / "yet"
    assert not target_dir.exists()
    with Storage(coach_workspace.db_path) as storage:
        report = carve_off_persona("Mike Chen", coach_workspace, storage, target_dir)
    assert Path(report.target_data_dir) == target_dir.resolve()
    assert target_dir.exists()


# --- CLI surface ------------------------------------------------------------


def test_cli_carve_off_persona_end_to_end(
    coach_workspace: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.cli.main import app

    monkeypatch.setenv(ENV_DATA_DIR, str(coach_workspace.data_dir))
    _seed_persona_capture(coach_workspace)

    target_dir = tmp_path / "mike-cli"
    result = runner.invoke(app, ["carve-off-persona", "Mike Chen", str(target_dir)])
    assert result.exit_code == 0, result.output
    assert "Carved off 'Mike Chen'" in result.stdout
    assert "Phase 2" in result.stdout

    with Storage(target_dir / "wingman.db") as storage:
        assert len(storage.list_profile_items()) == 1


def test_cli_carve_off_persona_reports_failure(
    coach_workspace: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.cli.main import app

    monkeypatch.setenv(ENV_DATA_DIR, str(coach_workspace.data_dir))
    result = runner.invoke(app, ["carve-off-persona", "Nobody", str(tmp_path / "x")])
    assert result.exit_code == 1
    assert "carve-off-persona failed" in result.output


def test_cli_carve_off_persona_requires_initialized_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.cli.main import app

    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "uninitialized"))
    result = runner.invoke(app, ["carve-off-persona", "Mike Chen", str(tmp_path / "target")])
    assert result.exit_code == 1
    assert "not initialized" in result.output


# --- MCP surface -------------------------------------------------------------


def test_mcp_carve_off_persona_end_to_end(
    coach_workspace: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.mcp_server import carve_off_persona as mcp_carve_off_persona

    monkeypatch.setenv(ENV_DATA_DIR, str(coach_workspace.data_dir))
    _seed_persona_capture(coach_workspace)

    target_dir = tmp_path / "mike-mcp"
    result = mcp_carve_off_persona("Mike Chen", str(target_dir))
    assert "Carved off 'Mike Chen'" in result
    assert "Phase 2" in result

    with Storage(target_dir / "wingman.db") as storage:
        assert len(storage.list_profile_items()) == 1


def test_mcp_carve_off_persona_reports_failure(
    coach_workspace: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.mcp_server import carve_off_persona as mcp_carve_off_persona

    monkeypatch.setenv(ENV_DATA_DIR, str(coach_workspace.data_dir))
    result = mcp_carve_off_persona("Nobody", str(tmp_path / "x"))
    assert "carve_off_persona failed" in result


def test_mcp_carve_off_persona_not_initialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.mcp_server import carve_off_persona as mcp_carve_off_persona

    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "uninitialized"))
    result = mcp_carve_off_persona("Mike Chen", str(tmp_path / "target"))
    assert "not initialized" in result
