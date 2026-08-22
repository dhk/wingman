"""Gap map: deterministic coverage of a rubric's dimensions (issue #436).

Every test builds its own workspace and its own rubric — no packaged file
is required for the behavioural tests, so a change to the shipped rubric's
wording can never silently change what these assert.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from wingman.application.gap_map import (
    EVIDENCED_THRESHOLD,
    MAX_CITATIONS,
    build_gap_map,
    render_gap_map,
)
from wingman.application.rubrics import RubricError, list_rubric_ids, load_all_rubrics, load_rubric
from wingman.domain.profile import (
    ClaimClassification,
    EvidenceSpan,
    ItemStatus,
    ProfileItem,
    ProfileItemKind,
)
from wingman.domain.rubric import Coverage, EvidenceVoice, ProvenanceTier, Rubric
from wingman.domain.source_record import SourceRecord
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return tmp_path


def _rubric(**overrides: object) -> Rubric:
    payload: dict[str, object] = {
        "id": "test-rubric",
        "title": "Test rubric",
        "version": "1",
        "provenance": {
            "tier": ProvenanceTier.RECONSTRUCTION,
            "sources": ["https://example.invalid/"],
            "disclaimer": "Not anybody's real document.",
            "license": "unspecified",
        },
        "dimensions": [
            {
                "id": "reach",
                "name": "Organisational reach",
                "asks": "How far does it reach?",
                "signals": ["across teams", "company-wide"],
                "probe": "Who else changed what they were doing?",
            },
            {
                "id": "ambiguity",
                "name": "Ambiguity absorbed",
                "asks": "How defined was it?",
                "signals": ["ambiguous", "nobody knew"],
                "probe": "What did people think the problem was?",
            },
        ],
    }
    payload.update(overrides)
    return Rubric.model_validate(payload)


def _add_item(
    storage: Storage,
    kind: ProfileItemKind,
    name: str,
    detail: str = "",
    *,
    status: ItemStatus = ItemStatus.ACTIVE,
    persona_id: str | None = None,
    subtype: str | None = None,
) -> ProfileItem:
    record = SourceRecord(
        source_type="test",
        source_locator="test://fixture",
        content_hash=f"hash-{name}-{status.value}-{persona_id}",
    )
    storage.add_source_record(record)
    item = ProfileItem(
        kind=kind,
        subtype=subtype,
        persona_id=persona_id,
        name=name,
        detail=detail,
        classification=ClaimClassification.FACT,
        confidence=1.0,
        evidence=[EvidenceSpan(source_record_id=record.record_id, quote=name)],
        status=status,
        prompt_version="test",
        extracted_by="test",
    )
    storage.add_profile_item(item)
    return item


def _gap(report: object, dimension_id: str) -> object:
    return next(row for row in report.dimensions if row.dimension_id == dimension_id)  # type: ignore[attr-defined]


def test_empty_workspace_reports_every_dimension_absent(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        report = build_gap_map(_rubric(), storage)
    assert report.items_read == 0
    assert [row.coverage for row in report.dimensions] == [Coverage.ABSENT, Coverage.ABSENT]


def test_matching_achievement_is_first_party_evidence(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _add_item(
            storage,
            ProfileItemKind.ACHIEVEMENT,
            "Rolled out tooling across teams",
        )
        report = build_gap_map(_rubric(), storage)
    reach = _gap(report, "reach")
    assert reach.coverage is Coverage.THIN
    assert reach.first_party == 1
    assert reach.third_party == 0
    assert reach.evidence[0].voice is EvidenceVoice.FIRST_PARTY
    assert reach.evidence[0].matched == ["across teams"]


def test_evidenced_needs_the_threshold(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        for index in range(EVIDENCED_THRESHOLD):
            _add_item(
                storage,
                ProfileItemKind.ACHIEVEMENT,
                f"Shipped thing {index} across teams",
            )
        report = build_gap_map(_rubric(), storage)
    assert _gap(report, "reach").coverage is Coverage.EVIDENCED


def test_testimonial_only_is_reported_as_third_party_only(workspace: Path) -> None:
    """A dimension nobody but a referee vouches for must not read as evidenced."""
    config = load_config()
    with Storage(config.db_path) as storage:
        for index in range(EVIDENCED_THRESHOLD + 2):
            _add_item(
                storage,
                ProfileItemKind.TESTIMONIAL,
                f"Recommendation {index}",
                detail="Changed how we worked across teams, company-wide.",
            )
        report = build_gap_map(_rubric(), storage)
    reach = _gap(report, "reach")
    assert reach.coverage is Coverage.THIRD_PARTY_ONLY
    assert reach.first_party == 0
    assert reach.third_party == EVIDENCED_THRESHOLD + 2


def test_interview_captures_are_never_read(workspace: Path) -> None:
    """An interview capture records a judgment about somebody else — crediting
    its text to the person would credit them for another's scope."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _add_item(
            storage,
            ProfileItemKind.INTERVIEW,
            "values_pro: Someone Else",
            detail="She drove change across teams, company-wide.",
            subtype="values_pro",
        )
        report = build_gap_map(_rubric(), storage)
    assert _gap(report, "reach").coverage is Coverage.ABSENT
    assert report.items_read == 0


def test_superseded_and_persona_items_are_excluded(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _add_item(
            storage,
            ProfileItemKind.ACHIEVEMENT,
            "Old claim across teams",
            status=ItemStatus.SUPERSEDED,
        )
        _add_item(
            storage,
            ProfileItemKind.ACHIEVEMENT,
            "Their claim across teams",
            persona_id="persona-1",
        )
        report = build_gap_map(_rubric(), storage)
    assert _gap(report, "reach").coverage is Coverage.ABSENT
    assert report.items_read == 0


def test_counts_are_not_truncated_by_the_citation_cap(workspace: Path) -> None:
    """The reader may see fewer citations than matches; never a smaller count."""
    config = load_config()
    matches = MAX_CITATIONS + 3
    with Storage(config.db_path) as storage:
        for index in range(matches):
            _add_item(storage, ProfileItemKind.ACHIEVEMENT, f"Thing {index} across teams")
        report = build_gap_map(_rubric(), storage)
    reach = _gap(report, "reach")
    assert reach.first_party == matches
    assert len(reach.evidence) == MAX_CITATIONS


def test_render_carries_the_disclaimer_and_refuses_to_position(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _add_item(storage, ProfileItemKind.ACHIEVEMENT, "Ran a thing across teams")
        report = build_gap_map(_rubric(), storage)
    rendered = render_gap_map(report)
    assert "Not anybody's real document." in rendered
    assert "does **not** place you on a rung" in rendered
    # The gap leads; the evidenced dimension follows.
    assert rendered.index("Nothing to read") < rendered.index("Thin")
    assert "What did people think the problem was?" in rendered


def test_cli_gap_map_runs_against_a_real_workspace(workspace: Path) -> None:
    from typer.testing import CliRunner

    from wingman.cli.main import app

    config = load_config()
    with Storage(config.db_path) as storage:
        _add_item(storage, ProfileItemKind.ACHIEVEMENT, "Architected a reconciliation pipeline")
    result = CliRunner().invoke(app, ["gap-map"])
    assert result.exit_code == 0, result.output
    assert "Gap map" in result.output
    assert "does **not** place you on a rung" in result.output


def test_cli_gap_map_rejects_an_unknown_rubric(workspace: Path) -> None:
    from typer.testing import CliRunner

    from wingman.cli.main import app

    config = load_config()
    with Storage(config.db_path):
        pass
    assert config.db_path.exists()
    result = CliRunner().invoke(app, ["gap-map", "--rubric", "nope"])
    assert result.exit_code == 1
    assert "unknown rubric" in result.output


def test_cli_rubrics_lists_provenance() -> None:
    from typer.testing import CliRunner

    from wingman.cli.main import app

    result = CliRunner().invoke(app, ["rubrics"])
    assert result.exit_code == 0, result.output
    assert "reconstruction" in result.output
    assert "NOT any single" in result.output


def test_unknown_rubric_names_what_is_available() -> None:
    with pytest.raises(RubricError) as excinfo:
        load_rubric("no-such-rubric")
    assert "available:" in str(excinfo.value)


def test_a_rubric_without_a_licence_is_refused() -> None:
    """'Publicly readable' is not 'ours to redistribute'. A rubric carrying an
    organisation's own descriptor text carries their copyright with it, so the
    terms are a required field — `unspecified` is the honest answer, and a
    missing one is not an answer at all."""
    payload = _rubric().model_dump()
    del payload["provenance"]["license"]
    with pytest.raises(ValidationError):
        Rubric.model_validate(payload)


def test_every_packaged_rubric_loads_and_declares_its_provenance() -> None:
    """A shipped rubric with a blank disclaimer would let a reconstruction be
    read as the real thing — the model requires one, this proves it holds
    for what actually ships."""
    rubrics = load_all_rubrics()
    assert rubrics, "no rubrics ship with the package"
    assert {rubric.id for rubric in rubrics} == set(list_rubric_ids())
    for rubric in rubrics:
        assert rubric.provenance.disclaimer.strip()
        assert rubric.provenance.license.strip()
        assert rubric.dimensions
        for dimension in rubric.dimensions:
            assert dimension.signals
            assert dimension.signals == [signal.lower() for signal in dimension.signals], (
                f"{rubric.id}/{dimension.id}: signals are matched lowercased, "
                "so an uppercase signal can never fire"
            )


def test_reach_and_impact_are_separate_dimensions_everywhere() -> None:
    """Dropbox separates organisational reach from business impact, and
    GitLab's public PM ladder puts them in different columns. Merging them
    into one axis is the conflation that over-positions strong individual
    contributors — so no shipped rubric may score them together."""
    for rubric in load_all_rubrics():
        ids = {dimension.id for dimension in rubric.dimensions}
        if "reach" in ids or "impact" in ids:
            assert {"reach", "impact"} <= ids, (
                f"{rubric.id}: reach and impact must both exist, or neither — "
                "one without the other is the merged axis by another name"
            )
