"""Asking whether a derived artefact is still true (issue #355).

Two ways one stops being true. New evidence arrived — already answerable,
and the model to follow. Or the CODE that shaped it changed — answerable
nowhere, until this. #340 is what the second costs: a change to the
scoring rule inverted the sign of every axis evidenced by a condemnation,
and every profile and every chart built beforehand went on asserting the
opposite of the truth. The charts sat in `reports/charts/` looking exactly
as authoritative as they had the day before.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.application.artifacts import remember_artifact
from wingman.application.freshness import (
    CHECKED_KINDS,
    current_fingerprint,
    render_staleness,
    stale_artefacts,
    unchecked_kinds,
    values_radar_fingerprint,
)
from wingman.application.pov import CORPUS_PERSON_ID, CORPUS_PERSON_NAME
from wingman.cli.main import app
from wingman.domain.artifacts import ARTIFACT_KINDS
from wingman.domain.values import (
    SCORING_CONTRACT_VERSION,
    ValueAxis,
    ValueAxisEvidence,
    ValueProfile,
)
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.reporting.radar import export_value_radar

runner = CliRunner()
URL = "https://claude.ai/code/artifact/a2979f3b-1fb2-4a34-8b76-421565dc29cc"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return config


def _axis(name: str, score: float = 0.5) -> ValueAxis:
    return ValueAxis(
        name=name,
        description=f"{name} description.",
        score=score,
        label="leans toward",
        evidence=[
            ValueAxisEvidence(
                item_id=f"item-{name}",
                subtype="values_pro",
                target="Jane Goodall",
                quote=f"{name} evidence quote.",
                intensity="strong",
                direction="supports",
                signed_weight=score,
            )
        ],
    )


def _profile(scoring_version: str = SCORING_CONTRACT_VERSION) -> ValueProfile:
    return ValueProfile(
        subject_id=CORPUS_PERSON_ID,
        subject_name=CORPUS_PERSON_NAME,
        axes=[_axis("A"), _axis("B"), _axis("C")],
        items_used=3,
        source_item_ids=["item-A", "item-B", "item-C"],
        provider="scripted",
        model="scripted-1",
        prompt_version="v1",
        scoring_version=scoring_version,
    )


def _only(config: Config, storage: Storage) -> object:
    reports = stale_artefacts(config, storage)
    assert len(reports) == 1
    return reports[0]


# --- the code half, which nothing could answer before -------------------------


def test_a_profile_scored_under_a_replaced_rule_is_reported_stale(workspace: Config) -> None:
    """The #340 case, generalized. Inputs are untouched — not one capture
    changed — and the artefact is still wrong, because the rule that turned
    those captures into numbers is not the rule this codebase runs."""
    with Storage(workspace.db_path) as storage:
        storage.save_value_profile(_profile(scoring_version="values-scoring-0"))
        report = _only(workspace, storage)

    assert report.stale
    assert any("values-scoring-0" in reason for reason in report.reasons)
    assert any("no longer runs" in reason for reason in report.reasons)


def test_an_unstamped_profile_is_stale_not_assumed_fine(workspace: Config) -> None:
    """Silence is read as 'unknown', never as 'current'. A profile with no
    scoring stamp predates the stamp, which means it predates #340's fix —
    the worst case, not the benign one."""
    with Storage(workspace.db_path) as storage:
        storage.save_value_profile(_profile(scoring_version=""))
        report = _only(workspace, storage)

    assert report.stale
    assert any("unrecorded" in reason for reason in report.reasons)


def test_a_current_profile_with_no_new_captures_is_reported_current(workspace: Config) -> None:
    """The control. A report that only ever says 'stale' is indistinguishable
    from one that failed to look."""
    with Storage(workspace.db_path) as storage:
        storage.save_value_profile(_profile())
        report = _only(workspace, storage)

    assert not report.stale
    assert report.checked


def test_new_captures_are_still_reported_alongside_the_code_half(workspace: Config) -> None:
    """The two kinds are independent and both have to show. Reporting only
    the one that happens to be checked first is how a person concludes a
    rebuild fixed everything."""
    from wingman.application.interview import capture_interview_reaction

    with Storage(workspace.db_path) as storage:
        storage.save_value_profile(_profile(scoring_version="values-scoring-0"))
        capture_interview_reaction(
            "values_pro",
            "New Person",
            "A brand new nomination.",
            workspace,
            storage,
            intensity="mild",
        )
        report = _only(workspace, storage)

    assert any("1 new capture" in reason for reason in report.reasons)
    assert any("values-scoring-0" in reason for reason in report.reasons)


# --- the artefacts that have left the workspace -------------------------------


def test_a_chart_on_disk_drawn_from_a_replaced_profile_is_caught(workspace: Config) -> None:
    """'A file sitting in reports/charts/ gave no hint' — the issue's own
    words. The file's embedded fingerprint names the profile it drew; a
    rebuild mints a new one, so the old chart stops matching."""
    with Storage(workspace.db_path) as storage:
        storage.save_value_profile(_profile())
        export_value_radar(workspace, storage)
        storage.save_value_profile(_profile())  # rebuilt: a new profile_id
        report = _only(workspace, storage)

    assert report.stale
    assert any("exported chart" in reason for reason in report.reasons)


def test_a_chart_on_disk_matching_the_stored_profile_is_not_flagged(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        storage.save_value_profile(_profile())
        export_value_radar(workspace, storage)
        report = _only(workspace, storage)

    assert not report.stale


def test_a_chart_with_no_provenance_stamp_is_reported_as_uncheckable(
    workspace: Config,
) -> None:
    """Every chart exported before this existed. 'Cannot tell' is reported,
    because the failure being addressed is an artefact that LOOKED fine."""
    charts = workspace.reports_dir / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    (charts / "you-corpus-values-radar.svg").write_text("<svg></svg>", encoding="utf-8")

    with Storage(workspace.db_path) as storage:
        storage.save_value_profile(_profile())
        report = _only(workspace, storage)

    assert any("carries no provenance stamp" in reason for reason in report.reasons)


def test_another_subjects_chart_is_not_reported_stale_against_yours(
    workspace: Config,
) -> None:
    """A coached persona's chart answers to their own profile. Calling it
    stale against the owner's would be a false alarm, and false alarms are
    what teach people to ignore a warning."""
    from wingman.application.coaching import find_or_create_persona
    from wingman.application.pov import persona_card_id

    with Storage(workspace.db_path) as storage:
        persona = find_or_create_persona("Mike Chen", storage)
        storage.save_value_profile(_profile())
        theirs = _profile()
        theirs.subject_id = persona_card_id(persona.persona_id)
        theirs.subject_name = "Mike Chen"
        storage.save_value_profile(theirs)
        export_value_radar(workspace, storage)
        export_value_radar(workspace, storage, persona=persona)
        report = _only(workspace, storage)

    assert not report.stale


def test_a_published_page_built_from_an_older_profile_is_caught(workspace: Config) -> None:
    """RFC-061 records where a page went; this is what makes that record
    useful. Without it every regeneration leaves another orphan that looks
    as authoritative as the last."""
    with Storage(workspace.db_path) as storage:
        first = _profile()
        storage.save_value_profile(first)
        remember_artifact(
            "values_radar",
            URL,
            storage,
            built_from=values_radar_fingerprint(first),
        )
        storage.save_value_profile(_profile())  # rebuilt since publishing
        report = _only(workspace, storage)

    assert report.stale
    assert any(URL in reason for reason in report.reasons)


def test_remembering_a_url_stamps_what_it_was_built_from(workspace: Config) -> None:
    """The stamp is applied at the surface, from the workspace, rather than
    asked of the caller: a fingerprint a model has to carry between two tool
    calls is one it can drop, and a wrong one is worse than none."""
    with Storage(workspace.db_path) as storage:
        profile = _profile()
        storage.save_value_profile(profile)
        Storage(workspace.db_path).close()

    assert runner.invoke(app, ["artifacts", "remember", "values_radar", URL]).exit_code == 0

    with Storage(workspace.db_path) as storage:
        recorded = storage.get_published_artifact("values_radar")
        assert recorded is not None
        assert recorded.built_from == values_radar_fingerprint(profile)
        assert not _only(workspace, storage).stale


def test_a_kind_with_no_stored_derivation_has_no_fingerprint(workspace: Config) -> None:
    """`completeness` and `profile` are rendered live every time, so there is
    nothing to compare a page against. An invented fingerprint would be a
    freshness claim with nothing behind it."""
    with Storage(workspace.db_path) as storage:
        storage.save_value_profile(_profile())
        assert current_fingerprint("completeness", storage) == ""
        assert current_fingerprint("values_radar", storage) != ""


# --- what the report says, including what it did not look at ------------------


def test_nothing_built_yet_is_reported_as_unchecked_not_as_current(
    workspace: Config,
) -> None:
    with Storage(workspace.db_path) as storage:
        report = _only(workspace, storage)

    assert not report.checked
    assert "nothing to be stale" in " ".join(report.reasons)


def test_the_report_names_the_kinds_it_did_not_examine(workspace: Config) -> None:
    """A staleness report quietly covering a third of the artefact kinds is
    the confident-freshness failure this mechanism exists to prevent,
    arriving by a different route."""
    with Storage(workspace.db_path) as storage:
        storage.save_value_profile(_profile())
        rendered = render_staleness(
            stale_artefacts(workspace, storage), unchecked_kinds(ARTIFACT_KINDS)
        )

    assert "Not checked:" in rendered
    for kind in ARTIFACT_KINDS:
        if kind not in CHECKED_KINDS:
            assert kind in rendered


def test_every_stale_line_carries_the_command_that_rebuilds_it(workspace: Config) -> None:
    """The issue asks for 'the exact command that rebuilds each'. A staleness
    report that leaves somebody hunting for the fix is a report they stop
    running."""
    with Storage(workspace.db_path) as storage:
        storage.save_value_profile(_profile(scoring_version="values-scoring-0"))
        rendered = render_staleness(stale_artefacts(workspace, storage), [])

    assert "STALE" in rendered
    assert "wingman values --refresh" in rendered
    assert "my_values(refresh=True)" in rendered


def test_the_cli_and_the_tool_give_the_same_verdict(workspace: Config) -> None:
    """RFC-008 parity: an operator in a terminal and an assistant in a
    conversation must not disagree about whether a chart is current."""
    with Storage(workspace.db_path) as storage:
        storage.save_value_profile(_profile(scoring_version="values-scoring-0"))
    Storage(workspace.db_path).close()

    from wingman.mcp_server import artifacts as artifacts_tool

    cli = runner.invoke(app, ["artifacts", "stale"])
    assert cli.exit_code == 0, cli.output
    assert "STALE" in cli.output
    assert "STALE" in artifacts_tool(action="stale")


def test_nothing_here_refuses_anything(workspace: Config) -> None:
    """Warn, never refuse — RFC-015's stale snapshots, RFC-056's render
    warning, and the same call made here. Refusing to draw a superseded
    chart takes away the only view of the evidence at the moment somebody
    is trying to work out whether to trust it."""
    with Storage(workspace.db_path) as storage:
        storage.save_value_profile(_profile(scoring_version="values-scoring-0"))
        export = export_value_radar(workspace, storage)
    Storage(workspace.db_path).close()

    assert export.path.exists()
    assert runner.invoke(app, ["artifacts", "stale"]).exit_code == 0
