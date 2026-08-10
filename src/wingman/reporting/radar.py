"""Radar/spider-chart rendering: v3 of issue #240 (docs/RFC.md RFC-052).

Pure presentation over an already-computed `domain.values.ValueProfile` (v2,
RFC-051) — no model call, nothing recomputed, no new scoring. Hand-rolled SVG
(stdlib `math` for the polar-coordinate geometry, an f-string template for
the markup) — AGENTS.md's minimal-dependency baseline rules out matplotlib/
plotly, and SVG is directly viewable in a browser, through the web UI
(`webui.py`'s existing `reports_dir` listing — see `_SERVE_TYPES`/
`_GROUP_NAMES`), or as a standalone file.

**The signed-score-on-a-radar design question (issue #240's own prompt).** A
radar chart conventionally plots magnitude outward from a shared center, but
`ValueAxis.score` is SIGNED ([-1.0, 1.0] — negative means "repelled by", not
merely "small"). Collapsing to bare magnitude (`abs(score)`) would erase that
sign entirely: a -0.9 axis and a +0.9 axis would draw as the identical
far-from-center vertex, which is actively misleading for a chart whose whole
point is "what does this person feel, and how strongly." Instead, radius is
a linear remap of the signed score onto [0, 1]:

    radius_fraction = (score + 1) / 2

so 0.0 (score = -1.0, "strongly repelled by") sits at the CENTER, a dashed
"neutral ring" at radius 0.5 marks score = 0 ("mixed / ambivalent"), and the
outer edge is score = +1.0 ("strongly drawn to"). This keeps the chart a
normal-looking radar (bigger shape reads as "more of this profile") while
still making a repelled-from axis visually distinct from a merely-small one
— it shrinks toward the center rather than vanishing into it or, worse,
plotting identically to its positive mirror. Nothing about the sign is left
implicit, either: every axis's raw signed `score` and its deterministic
`label` are printed as legend text below the chart AND carried in that
axis's vertex `<title>` (a hover tooltip when the SVG is opened directly or
embedded via `<object>`), alongside a short evidence excerpt — so the shape
and the number can always be cross-checked against each other, never one
without the other.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass
from pathlib import Path

from wingman.application.ingest import IngestError
from wingman.application.pov import CORPUS_PERSON_ID, persona_card_id
from wingman.application.values import new_captures_since
from wingman.domain.persona import Persona
from wingman.domain.values import ValueAxis, ValueProfile
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage
from wingman.reporting.design_tokens import DESIGN_TOKENS_CSS
from wingman.reporting.export import _slug

_logger = get_logger("reporting.radar")

# Geometry. Axis count is bounded 3-6 by application.values (MIN_AXES_REQUIRED/
# MAX_AXES) — this module trusts that bound rather than re-validating it.
_CENTER_X = 320.0
_CENTER_Y = 300.0
_MAX_RADIUS = 190.0
_LABEL_RADIUS = _MAX_RADIUS + 42.0
_VERTEX_RADIUS = 4.5
_RING_FRACTIONS = (0.25, 0.5, 0.75, 1.0)
_NEUTRAL_FRACTION = 0.5  # score == 0
_WIDTH = 640.0
_CHART_BOTTOM = _CENTER_Y + _MAX_RADIUS + 60.0
_LEGEND_ROW_HEIGHT = 30.0
_LEGEND_TOP_PAD = 20.0

_RULES_CSS = """\
svg.wingman-radar { background: var(--bg); }
.wingman-radar text { font-family: var(--font-sans); fill: var(--text); }
.wingman-radar .radar-title {
  font-family: var(--font-cond); font-weight: 700; font-size: 22px; fill: var(--text-head);
}
.wingman-radar .radar-meta {
  font-family: var(--font-mono); font-size: 11px; letter-spacing: 0.04em; fill: var(--text-dim);
}
.wingman-radar .radar-stale { font-size: 11px; fill: var(--accent-orange); }
.wingman-radar .radar-ring { fill: none; stroke: var(--border); stroke-width: 1; }
.wingman-radar .radar-ring-neutral { stroke: var(--text-dim); stroke-width: 1.25; stroke-dasharray: 4 3; }
.wingman-radar .radar-spoke { stroke: var(--border); stroke-width: 1; }
.wingman-radar .radar-axis-label { font-size: 13px; fill: var(--text-head); font-weight: 600; }
.wingman-radar .radar-shape {
  fill: var(--accent); fill-opacity: 0.22; stroke: var(--accent); stroke-width: 2;
  stroke-linejoin: round;
}
.wingman-radar .radar-vertex { fill: var(--accent); stroke: var(--bg); stroke-width: 1.5; }
.wingman-radar .radar-legend-name { font-size: 13px; font-weight: 600; fill: var(--text-head); }
.wingman-radar .radar-legend-score { font-family: var(--font-mono); font-size: 12px; fill: var(--text-muted); }
.wingman-radar .radar-legend-label { font-size: 12px; fill: var(--text-dim); }
"""

RADAR_CSS = DESIGN_TOKENS_CSS + _RULES_CSS


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _axis_angle(index: int, count: int) -> float:
    """Radians, starting at the top (12 o'clock) and proceeding clockwise —
    the conventional radar-chart orientation."""
    return -math.pi / 2 + index * (2 * math.pi / count)


def _polar(radius: float, angle: float) -> tuple[float, float]:
    return (_CENTER_X + radius * math.cos(angle), _CENTER_Y + radius * math.sin(angle))


def _radius_fraction(score: float) -> float:
    """The signed-to-radial remap this module's docstring documents: [-1, 1]
    -> [0, 1], 0.0 at the center, 1.0 at the outer edge, 0.5 the neutral ring."""
    return max(0.0, min(1.0, (score + 1.0) / 2.0))


def _label_anchor(angle: float) -> str:
    cos = math.cos(angle)
    if cos > 0.15:
        return "start"
    if cos < -0.15:
        return "end"
    return "middle"


def _evidence_excerpt(axis: ValueAxis) -> str:
    if not axis.evidence:
        return ""
    first = axis.evidence[0]
    quote = first.quote if len(first.quote) <= 140 else first.quote[:137] + "…"
    return f'"{quote}" — {first.target}'


def _ring_points(count: int, fraction: float) -> str:
    points = [_polar(_MAX_RADIUS * fraction, _axis_angle(i, count)) for i in range(count)]
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


def render_value_radar_svg(profile: ValueProfile, stale_new_captures: int = 0) -> str:
    """A self-contained SVG radar chart of `profile.axes` — see this module's
    docstring for the signed-score-to-radius convention. Never mutates or
    recomputes anything on `profile`; a pure rendering of the stored,
    already-scored contract."""
    axes = profile.axes
    count = len(axes)
    legend_bottom = _CHART_BOTTOM + _LEGEND_TOP_PAD + _LEGEND_ROW_HEIGHT * count
    stale_y = legend_bottom + _LEGEND_ROW_HEIGHT * 0.5
    height = legend_bottom + (_LEGEND_ROW_HEIGHT if stale_new_captures else 0.0) + 20.0

    # role="img" tells assistive technology to treat the whole chart as a
    # single image, so everything inside it — axis names, signed scores,
    # labels — stops being reachable. The accessible name alone says only
    # WHOSE chart this is. The <desc> is the text equivalent: the same
    # information the legend carries, in reading order.
    description = "; ".join(f"{axis.name} {axis.score:+.2f}, {axis.label}" for axis in axes)
    if stale_new_captures:
        description += f"; {stale_new_captures} new captures since this was built"
    desc_id = "radar-desc"

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WIDTH:.0f} {height:.0f}" '
        f'width="{_WIDTH:.0f}" height="{height:.0f}" class="wingman-radar" role="img" '
        f'aria-label="Value profile radar chart for {_e(profile.subject_name)}" '
        f'aria-describedby="{desc_id}">',
        f"<title>Value profile: {_e(profile.subject_name)}</title>",
        f'<desc id="{desc_id}">{_e(description)}</desc>',
        f"<style>{RADAR_CSS}</style>",
        f'<text class="radar-title" x="24" y="34">Value profile: {_e(profile.subject_name)}</text>',
        (
            f'<text class="radar-meta" x="24" y="54">'
            f"built from {profile.items_used} captured items · "
            f"{_e(profile.provider)}/{_e(profile.model)} · "
            f"{profile.generated_at.date().isoformat()}</text>"
        ),
    ]
    # Grid: one polygon ring per fraction, spokes from center to the outer
    # ring, one per axis. The 0.5 ring is dashed and separately classed —
    # this IS the score == 0 line the docstring promises.
    parts.append('<g class="radar-grid">')
    for fraction in _RING_FRACTIONS:
        css_class = (
            "radar-ring radar-ring-neutral" if fraction == _NEUTRAL_FRACTION else "radar-ring"
        )
        parts.append(f'<polygon class="{css_class}" points="{_ring_points(count, fraction)}"/>')
    for index in range(count):
        angle = _axis_angle(index, count)
        x, y = _polar(_MAX_RADIUS, angle)
        parts.append(
            f'<line class="radar-spoke" x1="{_CENTER_X:.2f}" y1="{_CENTER_Y:.2f}" '
            f'x2="{x:.2f}" y2="{y:.2f}"/>'
        )
    parts.append("</g>")

    # Data polygon: one vertex per axis, radius from the signed-score remap.
    vertex_points = []
    vertices_markup: list[str] = []
    for index, axis in enumerate(axes):
        angle = _axis_angle(index, count)
        radius = _MAX_RADIUS * _radius_fraction(axis.score)
        x, y = _polar(radius, angle)
        vertex_points.append((x, y))
        tooltip = f"{axis.name}: {axis.score:+.2f} — {axis.label}."
        if axis.description:
            tooltip += f" {axis.description}"
        excerpt = _evidence_excerpt(axis)
        if excerpt:
            tooltip += f" Evidence ({len(axis.evidence)}): {excerpt}"
        vertices_markup.append(
            f'<circle class="radar-vertex" data-axis="{_e(axis.name)}" '
            f'cx="{x:.2f}" cy="{y:.2f}" r="{_VERTEX_RADIUS}"><title>{_e(tooltip)}</title></circle>'
        )
    polygon_points = " ".join(f"{x:.2f},{y:.2f}" for x, y in vertex_points)
    parts.append('<g class="radar-shape-group">')
    parts.append(f'<polygon class="radar-shape" points="{polygon_points}"/>')
    parts.extend(vertices_markup)
    parts.append("</g>")

    # Axis name labels, placed just outside the outer ring, anchored toward
    # or away from the chart depending on which side they fall on.
    parts.append('<g class="radar-labels">')
    for index, axis in enumerate(axes):
        angle = _axis_angle(index, count)
        x, y = _polar(_LABEL_RADIUS, angle)
        anchor = _label_anchor(angle)
        parts.append(
            f'<text class="radar-axis-label" data-axis="{_e(axis.name)}" '
            f'x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}">{_e(axis.name)}</text>'
        )
    parts.append("</g>")

    # Legend: the raw signed score and deterministic label as plain text,
    # for the sign this chart's geometry alone can't fully convey.
    parts.append('<g class="radar-legend">')
    legend_top = _CHART_BOTTOM + _LEGEND_TOP_PAD
    for index, axis in enumerate(axes):
        row_y = legend_top + _LEGEND_ROW_HEIGHT * index
        parts.append(
            f'<text class="radar-legend-name" x="24" y="{row_y:.2f}">{_e(axis.name)}</text>'
            f'<text class="radar-legend-score" x="220" y="{row_y:.2f}">{axis.score:+.2f}</text>'
            f'<text class="radar-legend-label" x="280" y="{row_y:.2f}">{_e(axis.label)}</text>'
        )
    parts.append("</g>")

    # The staleness warning sits UNDER the legend, not at y=72 in the header.
    # The first axis label is always at y=68 — _LABEL_RADIUS is fixed and the
    # first spoke always points at 12 o'clock — so a warning at y=72 put two
    # text rows 4px apart and obscured both, on every stale chart at every
    # axis count. Down here it has the row to itself. (Shifting the whole
    # chart down when stale would keep it near the top, but that means two
    # different geometries to reason about for the sake of one line.)
    if stale_new_captures:
        noun = "capture" if stale_new_captures == 1 else "captures"
        parts.append(
            f'<text class="radar-stale" x="24" y="{stale_y:.2f}">'
            f"{stale_new_captures} new {noun} since this was built — rebuild with "
            "'wingman values --refresh' to include them</text>"
        )

    parts.append("</svg>")
    return "\n".join(parts)


def _resolve_chart_dir(config: Config, out_dir: Path | None) -> Path:
    """The chart destination: --out when given, reports/charts/ otherwise —
    the same "config.reports_dir subdirectory" convention `reporting.export`
    already established for `reports/pdf/`."""
    return (
        out_dir.expanduser() if out_dir is not None else config.reports_dir / "charts"
    ).resolve()


@dataclass(frozen=True)
class RadarExport:
    """Where the chart went, and whether what it shows is current.

    The stale count was embedded in the SVG and then dropped on the floor,
    because this returned a bare Path — so the CLI and MCP surfaces could
    not print the "N new captures" note RFC-052 promises they show, and
    only somebody who opened the file ever learned it was out of date.
    """

    path: Path
    stale_new_captures: int


def _write_svg(directory: Path, filename: str, svg: str) -> Path:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        path.write_text(svg, encoding="utf-8")
    except OSError as exc:
        # Both callers handle IngestError and print an actionable refusal.
        # Letting OSError through instead gave them a traceback for the
        # ordinary cases: a read-only --out, a full disk, no permission.
        raise IngestError(
            f"could not write the chart to {directory}: {exc}. Check the directory exists "
            "and is writable, or pass a different --out."
        ) from exc
    _logger.info("radar export path=%s", path)
    return path


def export_value_radar(
    config: Config,
    storage: Storage,
    persona: Persona | None = None,
    out_dir: Path | None = None,
) -> RadarExport:
    """Render the stored `ValueProfile` (own or, with `persona`, a coached
    Persona's — same scoping `application.values.build_value_profile` uses)
    as an SVG radar chart under `reports/charts/`, returning a `RadarExport`
    carrying both the path and the staleness count the callers report.

    No model call — this only reads whatever profile is already stored.
    Raises `IngestError`, same class of refusal `application.values` already
    uses for "not enough evidence yet", if no profile has been built for
    this subject: the caller is pointed at 'wingman values --refresh' /
    `my_values(refresh=True)` rather than getting an empty or fabricated
    chart. A stale profile (new captures since it was built) still renders —
    staleness is a warning printed on the chart, not a refusal, matching how
    `wingman values`/`my_values` already treat a stored-but-stale profile.
    """
    subject_id = persona_card_id(persona.persona_id) if persona is not None else CORPUS_PERSON_ID
    profile = storage.get_value_profile(subject_id)
    if profile is None:
        subject = persona.name if persona is not None else "you"
        raise IngestError(
            f"no value profile built for {subject} yet — run 'wingman values --refresh' "
            "(or my_values(refresh=True)) first, then try the chart again."
        )
    persona_id = persona.persona_id if persona is not None else None
    stale = new_captures_since(storage, profile, persona_id=persona_id)
    svg = render_value_radar_svg(profile, stale_new_captures=stale)
    directory = _resolve_chart_dir(config, out_dir)
    # The subject_id, not the display name alone. Slugs are lossy: 'A/B' and
    # 'A B' produce the same one, and a persona named "Your corpus" produces
    # the corpus chart's. Two subjects whose stored profiles are completely
    # isolated would then share a file, and the second export would silently
    # overwrite the first. The name still leads, so the file is recognizable.
    filename = f"{_slug(profile.subject_name)[:60]}-{_slug(subject_id)[:24]}-values-radar.svg"
    return RadarExport(path=_write_svg(directory, filename, svg), stale_new_captures=stale)
