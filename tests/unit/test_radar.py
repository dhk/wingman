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
    ValueView,
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

    subject, view, fingerprint = stamp_in(export.path.read_text(encoding="utf-8"))
    assert subject == CORPUS_PERSON_ID
    assert view is ValueView.CHARACTER
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


# --- a label drawn outside the viewBox is not drawn (#354) -------------------
#
# The bug arrived silently and was found by computing geometry, not by eye:
# four of five labels on a real chart ran past the canvas, the worst by 204px
# of 640. So the guard is a computation too, and the width model below is the
# TEST's own — deliberately not `radar._text_width`. A test that measures with
# the same function the renderer wraps with proves only that the renderer
# agrees with itself, which is exactly the reassurance the original code could
# also have offered.

#: The mean advance the issue measured on a real 13px chart. The renderer's
#: own estimate leans wider than this on purpose (see `_EM_*` in radar.py), so
#: a layout that satisfies the renderer has room to spare against reality.
_MEASURED_PX_PER_CHAR = 6.2

#: Axis names of the kind RFC-051 asks the model for. Two are verbatim from
#: real output; the rest are the same shape. Short ones are in the mix because
#: a fix that only ever wraps is as wrong as one that never does.
_REALISTIC_AXIS_NAMES = (
    "Compassion for the marginalized and downtrodden",
    "Intellectual openness and bridging opposing views",
    "Craft",
    "Institutional courage when speaking up costs something",
    "Loyalty to people over systems and process",
    "Directness",
)


def _measured_width(text: str) -> float:
    return _MEASURED_PX_PER_CHAR * len(text)


def _view_box(root: ET.Element) -> tuple[float, float]:
    _, _, width, height = (float(part) for part in (root.get("viewBox") or "").split())
    return width, height


def _label_lines(label: ET.Element) -> list[tuple[str, float]]:
    """Every drawn line of one axis label, with the baseline it lands on.

    A one-line label stays plain `<text>`; a wrapped one is `<tspan>` rows
    offset by `dy`. Both shapes have to be checked, because "it fits" must
    hold for the label that did not need wrapping too.
    """
    baseline = float(label.get("y") or 0.0)
    spans = label.findall(f"{SVG_NS}tspan")
    if not spans:
        return [(label.text or "", baseline)]
    lines: list[tuple[str, float]] = []
    for span in spans:
        baseline += float(span.get("dy") or 0.0)
        lines.append((span.text or "", baseline))
    return lines


def _horizontal_span(text: str, x: float, anchor: str) -> tuple[float, float]:
    width = _measured_width(text)
    if anchor == "start":
        return (x, x + width)
    if anchor == "end":
        return (x - width, x)
    return (x - width / 2.0, x + width / 2.0)


def _axis_labels(root: ET.Element) -> list[ET.Element]:
    return [t for t in _findall(root, "text") if t.get("class") == "radar-axis-label"]


@pytest.mark.parametrize("count", [3, 4, 5, 6])
def test_every_axis_label_stays_inside_the_viewbox(workspace: Path, count: int) -> None:
    """The regression guard for #354, at every axis count application.values
    allows (3-6). `viewBox` CLIPS: a label whose span leaves it is not merely
    tight, it is gone, and the reader has no way to tell the spoke was ever
    named."""
    axes = [_axis(name, 0.4) for name in _REALISTIC_AXIS_NAMES[:count]]
    root = _parse(render_value_radar_svg(_profile(axes)))
    width, height = _view_box(root)

    checked = 0
    for label in _axis_labels(root):
        x = _num(label, "x")
        anchor = label.get("text-anchor") or "start"
        for text, baseline in _label_lines(label):
            left, right = _horizontal_span(text, x, anchor)
            assert left >= 0.0, f"{text!r} runs {-left:.0f}px off the left edge"
            assert right <= width, f"{text!r} runs {right - width:.0f}px off the right edge"
            assert 0.0 < baseline < height, f"{text!r} is drawn outside the canvas vertically"
            checked += 1
    assert checked >= count  # the loop actually ran


def test_the_exact_chart_the_bug_was_reported_from_now_wraps(workspace: Path) -> None:
    """The reported case: five descriptive axis names, four of them clipped.
    Widening the canvas alone would have fixed that ONE profile and left the
    next longer name to overflow again, so the assertion is that the long
    side labels actually wrapped — the mechanism, not just the outcome."""
    axes = [_axis(name, 0.4) for name in _REALISTIC_AXIS_NAMES[:5]]
    root = _parse(render_value_radar_svg(_profile(axes)))

    wrapped = [label for label in _axis_labels(root) if len(_label_lines(label)) > 1]
    assert wrapped, "no label wrapped — the width budget is not being applied"
    assert any(label.get("text-anchor") == "start" for label in wrapped)
    assert any(label.get("text-anchor") == "end" for label in wrapped)


def test_a_wrapped_label_still_says_the_whole_axis_name(workspace: Path) -> None:
    """Truncation was rejected outright: an axis name IS the meaning of its
    spoke, and a silently cut one is worse than a wrapped one because the
    reader cannot tell anything was lost. Wrapping must therefore be
    lossless — every word, in order, no ellipsis."""
    axes = [_axis(name, 0.4) for name in _REALISTIC_AXIS_NAMES[:5]]
    svg = render_value_radar_svg(_profile(axes))
    root = _parse(svg)

    drawn = {
        label.get("data-axis"): " ".join(text for text, _ in _label_lines(label))
        for label in _axis_labels(root)
    }
    for axis in axes:
        assert drawn[axis.name] == axis.name
    assert "…" not in svg


def test_an_unbreakable_name_is_split_rather_than_left_to_overflow(workspace: Path) -> None:
    """A name with no space in it cannot be wrapped on one, which is exactly
    the input a space-only wrapper gets wrong — and getting it wrong means
    the original bug, back for one class of input."""
    monster = "Compassion" * 20  # 200 characters, no break opportunity
    axes = [_axis("A", 0.1), _axis(monster, 0.2), _axis("C", 0.3)]
    root = _parse(render_value_radar_svg(_profile(axes)))
    width, _ = _view_box(root)

    label = next(t for t in _axis_labels(root) if t.get("data-axis") == monster)
    lines = _label_lines(label)
    assert len(lines) > 1
    assert "".join(text for text, _ in lines) == monster  # nothing dropped
    for text, _ in lines:
        left, right = _horizontal_span(text, _num(label, "x"), label.get("text-anchor") or "start")
        assert left >= 0.0
        assert right <= width


def test_the_width_estimate_never_falls_below_what_a_real_chart_measured(
    workspace: Path,
) -> None:
    """There are no font metrics here — `--font-sans` is system-ui, so the
    face is the reader's — which makes the DIRECTION of the estimate's error
    the only thing under this module's control. Over-estimating wraps a line
    early and costs a reader nothing; under-estimating is #354."""
    from wingman.reporting.radar import _text_width

    for name in _REALISTIC_AXIS_NAMES:
        assert _text_width(name) >= _measured_width(name), f"{name!r} is measured too narrow"


def test_no_label_line_is_drawn_above_the_top_axis_row(workspace: Path) -> None:
    """#248's invariant, which vertical centring of a wrapped block would
    otherwise have quietly broken: the 12 o'clock label is the closest text
    to the header, and anything drawn above it lands on the meta line."""
    from wingman.reporting.radar import _CENTER_Y, _LABEL_RADIUS

    axes = [_axis(name, 0.4) for name in _REALISTIC_AXIS_NAMES[:4]]
    root = _parse(render_value_radar_svg(_profile(axes)))

    baselines = [baseline for label in _axis_labels(root) for _, baseline in _label_lines(label)]
    assert min(baselines) >= _CENTER_Y - _LABEL_RADIUS


def test_a_wrapped_bottom_label_pushes_the_legend_down_instead_of_landing_on_it(
    workspace: Path,
) -> None:
    """A label block's height is known before anything is drawn, so the room
    it needs is reserved — the same "reserve what you draw in" rule #248
    established for the notice rows. Without it, a bottom label that wraps is
    drawn straight through the legend's first line."""
    # 6 o'clock on a four-axis chart, long enough to wrap even centred.
    bottom = ("Institutional courage when speaking up costs something " * 3).strip()
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis(bottom, 0.3), _axis("D", 0.4)]
    short = _parse(render_value_radar_svg(_profile([_axis(n, 0.2) for n in "ABCD"])))
    root = _parse(render_value_radar_svg(_profile(axes)))

    label = next(t for t in _axis_labels(root) if t.get("data-axis") == bottom)
    lowest = max(baseline for _, baseline in _label_lines(label))
    legend_rows = [t for t in _findall(root, "text") if t.get("class") == "radar-legend-name"]

    assert len(_label_lines(label)) > 1
    assert lowest < min(_num(row, "y") for row in legend_rows)
    assert float(root.get("height") or 0.0) > float(short.get("height") or 0.0)


# --- one chart per view (#356) ------------------------------------------------


def _work_profile(axes: list[ValueAxis]) -> ValueProfile:
    return _profile(axes).model_copy(update={"view": ValueView.WORK})


def test_the_work_chart_is_its_own_file_and_never_overwrites_the_character_one(
    workspace: Path,
) -> None:
    """Same geometry, two readings, two files. One filename for both means
    the second export silently replaces the first, and whichever chart
    somebody opens is the one they believe."""
    config = load_config()
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
    with Storage(config.db_path) as storage:
        storage.save_value_profile(_profile(axes))
        storage.save_value_profile(_work_profile(axes))
        character = export_value_radar(config, storage)
        work = export_value_radar(config, storage, view=ValueView.WORK)

    assert character.path != work.path
    # The character view keeps its pre-#356 filename: renaming it would
    # orphan every chart already on disk and every link to one.
    assert character.path.name.endswith("-values-radar.svg")
    assert "-work-" in work.path.name
    assert character.path.exists() and work.path.exists()


def test_the_chart_names_the_view_it_draws(workspace: Path) -> None:
    """Two charts of the same person with differently-worded axes and only
    one of them labelled is how the wrong one gets quoted."""
    config = load_config()
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
    with Storage(config.db_path) as storage:
        storage.save_value_profile(_work_profile(axes))
        svg = render_value_radar_svg(storage.get_value_profile(CORPUS_PERSON_ID, ValueView.WORK))

    assert "Work profile (how you want to work)" in svg
    assert "Value profile (character view)" not in svg


def test_a_work_chart_does_not_report_the_character_chart_stale(workspace: Path) -> None:
    """The false alarm this would otherwise introduce. Both charts sit in
    reports/charts/ under the same subject; without a view marker in the
    stamp, each is judged against the other's profile and BOTH are reported
    stale forever — which is how somebody learns to ignore the warning that
    #340 needed them to read."""
    from wingman.application.freshness import stale_artefacts

    config = load_config()
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
    with Storage(config.db_path) as storage:
        storage.save_value_profile(_profile(axes))
        storage.save_value_profile(_work_profile(axes))
        export_value_radar(config, storage)
        export_value_radar(config, storage, view=ValueView.WORK)
        reports = stale_artefacts(config, storage)

    assert {report.kind for report in reports} == {"values_radar", "values_radar_work"}
    for report in reports:
        assert report.checked
        assert not report.stale, report.reasons


def test_a_nomination_only_work_chart_carries_its_caveat_on_the_picture(
    workspace: Path,
) -> None:
    """An SVG gets emailed away from every surface that would otherwise say
    the axes rest on character evidence — and a picture is the form most
    likely to be quoted at a hiring conversation."""
    config = load_config()
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
    with Storage(config.db_path) as storage:
        storage.save_value_profile(_work_profile(axes))
        export = export_value_radar(config, storage, view=ValueView.WORK)

    svg = export.path.read_text(encoding="utf-8")
    assert "no perspective reactions among the evidence" in svg
    # And it reaches a screen reader too: a warning sighted readers alone
    # can see is half a warning.
    root = ET.fromstring(svg)
    desc = root.find(f"{SVG_NS}desc")
    assert desc is not None and "no perspective reactions" in (desc.text or "")


def test_the_work_charts_rebuild_instruction_names_the_work_view(workspace: Path) -> None:
    """'wingman values --refresh' rebuilds the OTHER reading. An instruction
    that looks like it worked and rebuilt the wrong artefact is worse than
    no instruction."""
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
    stale_scoring = _work_profile(axes).model_copy(update={"scoring_version": "values-scoring-0"})
    svg = render_value_radar_svg(stale_scoring)
    assert "wingman values --refresh --view work" in svg


def test_the_export_stamp_records_which_view_it_drew(workspace: Path) -> None:
    from wingman.application.freshness import stamp_in

    config = load_config()
    axes = [_axis("A", 0.1), _axis("B", 0.2), _axis("C", 0.3)]
    with Storage(config.db_path) as storage:
        storage.save_value_profile(_work_profile(axes))
        export = export_value_radar(config, storage, view=ValueView.WORK)

    _subject, view, _fingerprint = stamp_in(export.path.read_text(encoding="utf-8"))
    assert view is ValueView.WORK
