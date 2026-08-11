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
from wingman.domain.values import (
    RADAR_CONTRACT_VERSION,
    SCORING_CONTRACT_VERSION,
    ValueAxis,
    ValueAxisEvidence,
    ValueProfile,
)
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
                direction="supports" if score >= 0 else "opposes",
                signed_weight=score,
            )
        ],
    )


def _profile(
    axes: list[ValueAxis],
    subject_id: str = CORPUS_PERSON_ID,
    subject_name: str = CORPUS_PERSON_NAME,
    scoring_version: str = SCORING_CONTRACT_VERSION,
) -> ValueProfile:
    """A profile scored under the CURRENT contract unless a test says
    otherwise — the default has to be the ordinary case, or every chart in
    this module would render carrying a superseded-scoring warning and the
    tests about the ordinary chart would stop being about it (#355)."""
    return ValueProfile(
        subject_id=subject_id,
        subject_name=subject_name,
        axes=axes,
        items_used=len(axes) * 2,
        source_item_ids=[f"item-{a.name}" for a in axes],
        provider="scripted",
        model="scripted-1",
        prompt_version="v1",
        scoring_version=scoring_version,
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
    import math

    from wingman.reporting.radar import _CENTER_X, _CENTER_Y

    axes = [_axis("Pos", 0.8), _axis("Neg", -0.8), _axis("Filler", 0.0)]
    svg = render_value_radar_svg(_profile(axes))
    root = _parse(svg)
    pos = next(v for v in _findall(root, "circle") if v.get("data-axis") == "Pos")
    neg = next(v for v in _findall(root, "circle") if v.get("data-axis") == "Neg")

    # DISTANCE FROM THE CENTRE, not a raw coordinate. These two vertices sit
    # on different spokes, so their cy values differ by geometry alone —
    # comparing them passed even under bare-magnitude plotting, which is the
    # exact bug this test exists to catch.
    def radius(vertex: object) -> float:
        return math.hypot(_num(vertex, "cx") - _CENTER_X, _num(vertex, "cy") - _CENTER_Y)

    assert radius(pos) > radius(neg)
    assert radius(pos) != pytest.approx(radius(neg), abs=0.5)


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
        export = export_value_radar(config, storage)
    path = export.path
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
        export = export_value_radar(config, storage)
    text = export.path.read_text(encoding="utf-8")
    assert "1 new capture" in text
    # ...and the caller is told too, so it can say so without opening the file.
    assert export.stale_new_captures == 1


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
        export = export_value_radar(config, storage, persona=persona)
    assert "mike-chen" in export.path.name


def test_export_out_dir_overrides_default(workspace: Path, tmp_path: Path) -> None:
    config = load_config()
    custom = tmp_path / "custom-out"
    with Storage(config.db_path) as storage:
        axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
        storage.save_value_profile(_profile(axes))
        export = export_value_radar(config, storage, out_dir=custom)
    assert export.path.parent == custom.resolve()


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
    from wingman import mcp_server

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
    from wingman import mcp_server

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


# --- what the review caught (#248) --------------------------------------------


def test_the_stale_warning_does_not_land_on_the_top_axis_label(workspace: Path) -> None:
    """The first axis label is always at y=68 — _LABEL_RADIUS is fixed and the
    first spoke always points at 12 o'clock — while the warning was drawn at
    y=72. Two 11-13px text rows 4px apart obscure each other, so every stale
    chart hid both its warning and its top axis name."""
    from wingman.reporting.radar import _CENTER_Y, _LABEL_RADIUS

    axes = [_axis("Steadfastness", 0.4), _axis("B", 0.2), _axis("C", 0.3)]
    svg = render_value_radar_svg(_profile(axes), stale_new_captures=3)
    root = _parse(svg)

    top_label_y = _CENTER_Y - _LABEL_RADIUS  # 12 o'clock
    warning = next(t for t in _findall(root, "text") if "3 new captures" in (t.text or ""))

    assert abs(_num(warning, "y") - top_label_y) > 20.0


def test_a_stale_chart_reserves_the_room_it_draws_in(workspace: Path) -> None:
    """Moving the warning below the legend only helps if the viewBox grows to
    match — otherwise it is drawn outside the visible area, which is worse
    than overlapping."""
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
    fresh = _parse(render_value_radar_svg(_profile(axes)))
    stale = _parse(render_value_radar_svg(_profile(axes), stale_new_captures=1))

    warning = next(t for t in _findall(stale, "text") if "1 new capture" in (t.text or ""))
    stale_height = float(stale.get("height", "0"))

    assert stale_height > float(fresh.get("height", "0"))
    assert _num(warning, "y") < stale_height


def test_two_subjects_whose_names_slug_alike_do_not_overwrite_each_other(
    workspace: Path,
) -> None:
    """Slugs are lossy: 'A/B' and 'A B' produce the same one. Two subjects
    whose stored profiles are completely isolated would then share a filename,
    and exporting the second silently destroyed the first."""
    from wingman.application.coaching import find_or_create_persona

    config = load_config()
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
    with Storage(config.db_path) as storage:
        first = find_or_create_persona("A/B", storage)
        second = find_or_create_persona("A B", storage)
        for persona in (first, second):
            storage.save_value_profile(
                _profile(
                    axes,
                    subject_id=persona_card_id(persona.persona_id),
                    subject_name=persona.name,
                )
            )
        one = export_value_radar(config, storage, persona=first)
        two = export_value_radar(config, storage, persona=second)

    assert one.path != two.path
    assert one.path.exists() and two.path.exists()


def test_a_persona_cannot_collide_with_the_corpus_chart(workspace: Path) -> None:
    """A persona named 'Your corpus' slugs to whatever the corpus subject
    does — different stored profiles, one file."""
    from wingman.application.coaching import find_or_create_persona

    config = load_config()
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
    with Storage(config.db_path) as storage:
        storage.save_value_profile(_profile(axes))
        mine = export_value_radar(config, storage)
        impostor = find_or_create_persona(_profile(axes).subject_name, storage)
        storage.save_value_profile(
            _profile(
                axes,
                subject_id=persona_card_id(impostor.persona_id),
                subject_name=impostor.name,
            )
        )
        theirs = export_value_radar(config, storage, persona=impostor)

    assert mine.path != theirs.path


def test_an_unwritable_destination_is_a_refusal_not_a_traceback(
    workspace: Path, tmp_path: Path
) -> None:
    """Both callers handle IngestError and print an actionable refusal. The
    write is mkdir() + write_text(), which raise OSError — so a read-only
    --out, a full disk or a permissions problem bypassed all of that."""
    config = load_config()
    blocked = tmp_path / "no-entry"
    blocked.mkdir()
    blocked.chmod(0o500)  # readable, not writable
    try:
        with Storage(config.db_path) as storage:
            axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
            storage.save_value_profile(_profile(axes))
            with pytest.raises(IngestError, match="could not write the chart"):
                export_value_radar(config, storage, out_dir=blocked / "charts")
    finally:
        blocked.chmod(0o700)


def test_the_chart_carries_a_text_equivalent_of_what_it_plots(workspace: Path) -> None:
    """role="img" makes assistive technology treat the whole SVG as one
    image, so the axis names, signed scores and labels inside it stop being
    reachable. The accessible name alone says only whose chart this is."""
    axes = [
        _axis("Steadfastness", -0.45, label="leans away from"),
        _axis("Candour", 0.8, label="leans toward"),
        _axis("C", 0.3),
    ]
    root = _parse(render_value_radar_svg(_profile(axes)))

    desc = next(iter(_findall(root, "desc")))
    assert root.get("aria-describedby") == desc.get("id")
    assert "Steadfastness -0.45, leans away from" in (desc.text or "")
    assert "Candour +0.80, leans toward" in (desc.text or "")


# --- what an exported chart says about itself (#355, amending RFC-056) --------


def _pre_direction_axis(name: str, score: float) -> ValueAxis:
    """An axis whose evidence carries no per-item direction — the shape of
    every profile stored before RFC-056, i.e. every profile whose signs the
    #340 bug may have inverted."""
    return ValueAxis(
        name=name,
        description=f"{name} description.",
        score=score,
        label="leans toward",
        evidence=[
            ValueAxisEvidence(
                item_id=f"item-{name}",
                subtype="values_con",
                target="Jane Goodall",
                quote=f"{name} evidence quote.",
                intensity="strong",
                direction=None,
                signed_weight=score,
            )
        ],
    )


def test_a_chart_of_a_pre_340_profile_warns_on_the_chart_itself(workspace: Path) -> None:
    """RFC-056 accepted this gap explicitly: a ValueProfile stored before the
    sign fix rendered with a warning, but the SVG carried none — so an
    exported chart went on drawing the inverted shape, and the owner's chart
    said 'strongly repelled by honesty' for hours with nothing on it to say
    the numbers were suspect. The concession assumed a one-time migration
    window. It is not one; the scoring rule will move again (#355).
    """
    axes = [_pre_direction_axis("Honesty", 0.4), _axis("B", 0.2), _axis("C", 0.3)]
    svg = render_value_radar_svg(_profile(axes, scoring_version=""))

    root = _parse(svg)
    texts = [t.text or "" for t in _findall(root, "text")]
    assert any("may be inverted" in text for text in texts)
    assert any("wingman values --refresh" in text for text in texts)


def test_a_superseded_scoring_contract_warns_even_with_directions_recorded(
    workspace: Path,
) -> None:
    """The general case, not only #340's: any profile scored under a rule
    this codebase no longer runs. A future contract change has no way to
    know in advance what it broke, so the chart says the shape may be
    superseded and names both versions."""
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
    svg = render_value_radar_svg(_profile(axes, scoring_version="values-scoring-0"))

    root = _parse(svg)
    texts = [t.text or "" for t in _findall(root, "text")]
    assert any("values-scoring-0" in text and SCORING_CONTRACT_VERSION in text for text in texts)


def test_a_current_chart_carries_no_warning_at_all(workspace: Path) -> None:
    """A warning that is always there is not a warning. This is the control
    for the two above."""
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
    svg = render_value_radar_svg(_profile(axes))

    texts = [t.text or "" for t in _findall(_parse(svg), "text")]
    assert not any("superseded" in text or "inverted" in text for text in texts)


def test_both_warnings_get_their_own_row_and_the_viewbox_grows_for_them(
    workspace: Path,
) -> None:
    """A chart can be out of date both ways at once. Stacking two warnings on
    one row hides both, and drawing them past the viewBox hides both as
    well — which is how the stale note failed the first time (#248)."""
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
    current = _parse(render_value_radar_svg(_profile(axes)))
    both = _parse(render_value_radar_svg(_profile(axes, scoring_version=""), stale_new_captures=2))

    warnings = [
        t
        for t in _findall(both, "text")
        if "superseded" in (t.text or "")
        or "re-export" in (t.text or "")
        or "2 new captures" in (t.text or "")
    ]
    height = float(both.get("height", "0"))

    assert len(warnings) == 3  # headline, rebuild instruction, stale count
    assert len({_num(t, "y") for t in warnings}) == 3  # no two share a row
    assert height > float(current.get("height", "0"))
    assert all(_num(t, "y") < height for t in warnings)


def test_the_chart_names_the_contracts_that_drew_it_even_when_current(
    workspace: Path,
) -> None:
    """AGENTS.md: a score must expose the scoring-rule version. A version
    that only appears when something is wrong teaches nobody what right
    looks like, and a reader sent a chart cannot ask the workspace."""
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
    texts = [t.text or "" for t in _findall(_parse(render_value_radar_svg(_profile(axes))), "text")]

    meta = next(text for text in texts if "built from" in text)
    assert SCORING_CONTRACT_VERSION in meta
    assert RADAR_CONTRACT_VERSION in meta


def test_an_exported_file_can_be_judged_without_the_workspace(workspace: Path) -> None:
    """The provenance stamp is what lets 'wingman artifacts stale' tell a
    chart on disk from the profile it should have been drawn from. A comment
    rather than drawn text: it must not be noise on the picture, and it does
    not need to be — the visible warning is what a reader needs."""
    from wingman.application.freshness import stamp_in, values_radar_fingerprint

    config = load_config()
    with Storage(config.db_path) as storage:
        axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
        profile = _profile(axes)
        storage.save_value_profile(profile)
        export = export_value_radar(config, storage)

    subject, fingerprint = stamp_in(export.path.read_text(encoding="utf-8"))
    assert subject == CORPUS_PERSON_ID
    assert fingerprint == values_radar_fingerprint(profile) == export.fingerprint


def test_the_surfaces_repeat_the_superseded_warning_without_opening_the_file(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Somebody who never opens the SVG still has to hear it — the same
    mistake the stale count made when export returned a bare Path."""
    from typer.testing import CliRunner

    from wingman.cli.main import app
    from wingman.infrastructure.config import ENV_DATA_DIR

    config = load_config()
    with Storage(config.db_path) as storage:
        axes = [_pre_direction_axis("Honesty", 0.4), _axis("B", 0.2), _axis("C", 0.3)]
        storage.save_value_profile(_profile(axes, scoring_version=""))
        assert export_value_radar(config, storage).scoring_superseded

    monkeypatch.setenv(ENV_DATA_DIR, str(config.data_dir))
    result = CliRunner().invoke(app, ["values-chart"])
    assert result.exit_code == 0, result.output
    assert "no longer runs" in result.output

    from wingman.mcp_server import values_chart as values_chart_tool

    assert "no longer runs" in values_chart_tool()


def test_a_superseded_chart_still_renders_rather_than_refusing(workspace: Path) -> None:
    """Warn, do not refuse (RFC-015, RFC-056). Somebody asking for the chart
    of a profile they have been told is suspect is usually asking precisely
    in order to see how suspect it is."""
    config = load_config()
    with Storage(config.db_path) as storage:
        axes = [_pre_direction_axis("Honesty", 0.4), _axis("B", 0.2), _axis("C", 0.3)]
        storage.save_value_profile(_profile(axes, scoring_version=""))
        export = export_value_radar(config, storage)

    assert export.path.exists()
    assert "<polygon" in export.path.read_text(encoding="utf-8")
