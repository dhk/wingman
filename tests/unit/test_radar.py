"""Radar-chart rendering (v3 of issue #240, docs/RFC.md RFC-052): the
signed-score-to-radius geometry, labels/legend, the stale/no-profile refusal
path, the reports/charts/ file-location convention, and the CLI+MCP surface.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.pov import CORPUS_PERSON_ID, CORPUS_PERSON_NAME, persona_card_id
from wingman.domain.values import ValueAxis, ValueAxisEvidence, ValueProfile
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.reporting.radar import (
    _CENTER_X,
    _CENTER_Y,
    _MAX_RADIUS,
    export_value_radar,
    render_value_radar_svg,
)

SVG_NS = "{http://www.w3.org/2000/svg}"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return tmp_path


def _axis(name: str, score: float, label: str = "leans toward") -> ValueAxis:
    return ValueAxis(
        name=name,
        description=f"{name} description.",
        score=score,
        label=label,
        evidence=[
            ValueAxisEvidence(
                item_id=f"item-{name}",
                subtype="values_pro" if score >= 0 else "values_con",
                target="Jane Goodall",
                quote=f"{name} evidence quote.",
                intensity="strong",
                signed_weight=score,
            )
        ],
    )


def _profile(
    axes: list[ValueAxis],
    subject_id: str = CORPUS_PERSON_ID,
    subject_name: str = CORPUS_PERSON_NAME,
) -> ValueProfile:
    return ValueProfile(
        subject_id=subject_id,
        subject_name=subject_name,
        axes=axes,
        items_used=len(axes) * 2,
        source_item_ids=[f"item-{a.name}" for a in axes],
        provider="scripted",
        model="scripted-1",
        prompt_version="v1",
    )


def _parse(svg: str) -> ET.Element:
    return ET.fromstring(svg)


def _findall(root: ET.Element, tag: str) -> list[ET.Element]:
    return root.findall(f".//{SVG_NS}{tag}")


def _num(element: ET.Element, attr: str) -> float:
    value = element.get(attr)
    assert value is not None
    return float(value)


# --- structural properties: one spoke/vertex/label per axis -----------------


def test_one_vertex_spoke_and_label_per_axis(workspace: Path) -> None:
    axes = [_axis("Curiosity", 0.4), _axis("Loyalty", -0.6), _axis("Craft", 0.9)]
    svg = render_value_radar_svg(_profile(axes))
    root = _parse(svg)

    vertices = [c for c in _findall(root, "circle") if c.get("class") == "radar-vertex"]
    spokes = _findall(root, "line")
    labels = [t for t in _findall(root, "text") if t.get("class") == "radar-axis-label"]

    assert len(vertices) == len(axes)
    assert len(spokes) == len(axes)
    assert len(labels) == len(axes)
    assert {v.get("data-axis") for v in vertices} == {a.name for a in axes}
    assert {label.text for label in labels} == {a.name for a in axes}


def test_data_polygon_has_one_vertex_pair_per_axis(workspace: Path) -> None:
    axes = [_axis("A", 0.1), _axis("B", -0.2), _axis("C", 0.3), _axis("D", 0.7)]
    svg = render_value_radar_svg(_profile(axes))
    root = _parse(svg)
    shape = next(p for p in _findall(root, "polygon") if p.get("class") == "radar-shape")
    points = shape.get("points", "").split()
    assert len(points) == len(axes)


def test_svg_is_well_formed_xml_and_carries_the_subject_name(workspace: Path) -> None:
    axes = [_axis("A", 0.1), _axis("B", -0.2), _axis("C", 0.3)]
    svg = render_value_radar_svg(_profile(axes, subject_name="Ada Lovelace"))
    root = _parse(svg)  # raises ParseError if malformed
    assert root.tag == f"{SVG_NS}svg"
    title = root.find(f"{SVG_NS}title")
    assert title is not None and "Ada Lovelace" in (title.text or "")


# --- the signed-score-to-radius convention -----------------------------------


def test_score_minus_one_plots_at_the_center(workspace: Path) -> None:
    axes = [_axis("Repelled", -1.0), _axis("Filler1", 0.0), _axis("Filler2", 0.0)]
    svg = render_value_radar_svg(_profile(axes))
    root = _parse(svg)
    vertex = next(v for v in _findall(root, "circle") if v.get("data-axis") == "Repelled")
    # axis index 0 sits at the top (12 o'clock): x stays at center, y == center - radius.
    assert _num(vertex, "cx") == pytest.approx(_CENTER_X, abs=0.5)
    assert _num(vertex, "cy") == pytest.approx(_CENTER_Y, abs=0.5)


def test_score_plus_one_plots_at_the_outer_edge(workspace: Path) -> None:
    axes = [_axis("Drawn", 1.0), _axis("Filler1", 0.0), _axis("Filler2", 0.0)]
    svg = render_value_radar_svg(_profile(axes))
    root = _parse(svg)
    vertex = next(v for v in _findall(root, "circle") if v.get("data-axis") == "Drawn")
    assert _num(vertex, "cx") == pytest.approx(_CENTER_X, abs=0.5)
    assert _num(vertex, "cy") == pytest.approx(_CENTER_Y - _MAX_RADIUS, abs=0.5)


def test_score_zero_plots_at_the_neutral_ring_halfway_out(workspace: Path) -> None:
    axes = [_axis("Neutral", 0.0), _axis("Filler1", 0.0), _axis("Filler2", 0.0)]
    svg = render_value_radar_svg(_profile(axes))
    root = _parse(svg)
    vertex = next(v for v in _findall(root, "circle") if v.get("data-axis") == "Neutral")
    assert _num(vertex, "cy") == pytest.approx(_CENTER_Y - _MAX_RADIUS / 2, abs=0.5)
    neutral_ring = next(
        p for p in _findall(root, "polygon") if p.get("class") == "radar-ring radar-ring-neutral"
    )
    assert neutral_ring is not None


def test_negative_and_positive_axes_of_equal_magnitude_plot_differently(workspace: Path) -> None:
    """The core design question this slice had to resolve: a -0.8 axis must
    NOT draw identically to a +0.8 axis (bare-magnitude plotting would erase
    the sign entirely)."""
    axes = [_axis("Pos", 0.8), _axis("Neg", -0.8), _axis("Filler", 0.0)]
    svg = render_value_radar_svg(_profile(axes))
    root = _parse(svg)
    pos = next(v for v in _findall(root, "circle") if v.get("data-axis") == "Pos")
    neg = next(v for v in _findall(root, "circle") if v.get("data-axis") == "Neg")
    assert _num(pos, "cy") != pytest.approx(_num(neg, "cy"), abs=0.5)


# --- the sign is never lost: legend text + tooltip ---------------------------


def test_legend_prints_signed_score_and_label_text(workspace: Path) -> None:
    axes = [
        _axis("Steadfastness", -0.45, label="leans away from"),
        _axis("B", 0.2),
        _axis("C", 0.3),
    ]
    svg = render_value_radar_svg(_profile(axes))
    assert "-0.45" in svg
    assert "leans away from" in svg
    assert "Steadfastness" in svg


def test_vertex_tooltip_carries_score_label_and_evidence_excerpt(workspace: Path) -> None:
    axes = [
        _axis("Steadfastness", 0.6, label="strongly drawn to"),
        _axis("B", 0.1),
        _axis("C", 0.2),
    ]
    svg = render_value_radar_svg(_profile(axes))
    root = _parse(svg)
    vertex = next(v for v in _findall(root, "circle") if v.get("data-axis") == "Steadfastness")
    tooltip = vertex.find(f"{SVG_NS}title")
    assert tooltip is not None
    text = tooltip.text or ""
    assert "+0.60" in text
    assert "strongly drawn to" in text
    assert "Jane Goodall" in text  # the evidence excerpt's target


def test_stale_note_appears_when_new_captures_given(workspace: Path) -> None:
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
    svg = render_value_radar_svg(_profile(axes), stale_new_captures=2)
    assert "2 new captures" in svg
    assert "wingman values --refresh" in svg


def test_no_stale_note_when_zero(workspace: Path) -> None:
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
    svg = render_value_radar_svg(_profile(axes), stale_new_captures=0)
    assert "new capture" not in svg


# --- export_value_radar: file location, refusal, staleness ------------------


def test_export_writes_under_reports_charts(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
        storage.save_value_profile(_profile(axes))
        path = export_value_radar(config, storage)
    assert path.parent == config.reports_dir / "charts"
    assert path.suffix == ".svg"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "<svg" in content


def test_export_refuses_without_a_stored_profile(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="no value profile built"):
            export_value_radar(config, storage)


def test_export_refusal_names_the_refresh_command(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="wingman values --refresh"):
            export_value_radar(config, storage)


def test_export_still_renders_when_stale_and_notes_it(workspace: Path) -> None:
    from wingman.application.interview import capture_interview_reaction

    config = load_config()
    with Storage(config.db_path) as storage:
        axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
        storage.save_value_profile(_profile(axes))
        capture_interview_reaction(
            "values_pro", "New Person", "A brand new nomination.", config, storage, intensity="mild"
        )
        path = export_value_radar(config, storage)
    text = path.read_text(encoding="utf-8")
    assert "1 new capture" in text


def test_export_persona_scoping(workspace: Path) -> None:
    from wingman.application.coaching import find_or_create_persona

    config = load_config()
    with Storage(config.db_path) as storage:
        persona = find_or_create_persona("Mike Chen", storage)
        axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
        storage.save_value_profile(
            _profile(axes, subject_id=persona_card_id(persona.persona_id), subject_name="Mike Chen")
        )
        # the persona's profile must not satisfy the coach's own (unscoped) chart
        with pytest.raises(IngestError, match="no value profile built for you yet"):
            export_value_radar(config, storage)
        path = export_value_radar(config, storage, persona=persona)
    assert "mike-chen" in path.name


def test_export_out_dir_overrides_default(workspace: Path, tmp_path: Path) -> None:
    config = load_config()
    custom = tmp_path / "custom-out"
    with Storage(config.db_path) as storage:
        axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
        storage.save_value_profile(_profile(axes))
        path = export_value_radar(config, storage, out_dir=custom)
    assert path.parent == custom.resolve()


# --- CLI + MCP surface --------------------------------------------------------


def test_cli_values_chart_writes_and_reports_path(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from wingman.cli.main import app
    from wingman.infrastructure.config import ENV_DATA_DIR

    config = load_config()
    with Storage(config.db_path) as storage:
        axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
        storage.save_value_profile(_profile(axes))

    monkeypatch.setenv(ENV_DATA_DIR, str(config.data_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["values-chart"])
    assert result.exit_code == 0, result.output
    assert "Acting as: yourself." in result.output
    assert "Wrote" in result.output
    assert str(config.reports_dir / "charts") in result.output


def test_cli_values_chart_without_a_profile_fails_cleanly(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from wingman.cli.main import app
    from wingman.infrastructure.config import ENV_DATA_DIR

    config = load_config()
    Storage(config.db_path).close()
    monkeypatch.setenv(ENV_DATA_DIR, str(config.data_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["values-chart"])
    assert result.exit_code == 1
    assert "no value profile built" in result.output


def test_mcp_values_chart_writes_and_reports_path(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import wingman.mcp_server as mcp_server

    config = load_config()
    with Storage(config.db_path) as storage:
        axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
        storage.save_value_profile(_profile(axes))

    result = mcp_server.values_chart()
    assert "Acting as: yourself." in result
    assert "Wrote" in result
    assert str(config.reports_dir / "charts") in result


def test_mcp_values_chart_without_a_profile_returns_a_message_not_a_traceback(
    workspace: Path,
) -> None:
    import wingman.mcp_server as mcp_server

    config = load_config()
    Storage(config.db_path).close()
    result = mcp_server.values_chart()
    assert "values-chart failed" in result
    assert "no value profile built" in result


def test_mcp_values_chart_reads_active_persona(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.application.coaching import find_or_create_persona
    from wingman.mcp_server import coach_persona, values_chart

    config = load_config()
    with Storage(config.db_path) as storage:
        persona = find_or_create_persona("Mike Chen", storage)
        axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
        storage.save_value_profile(
            _profile(axes, subject_id=persona_card_id(persona.persona_id), subject_name="Mike Chen")
        )

    coach_persona("set", "Mike Chen")
    result = values_chart()
    assert "Acting as: coach for Mike Chen." in result
    assert "mike-chen" in result

    coach_persona("clear")
