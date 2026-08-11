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

**What a file that has left the workspace can say for itself (issue #355,
RFC-063, amending RFC-056).**
This module produces the one artefact that has already been wrong in the
field: #340 inverted every axis evidenced by a condemnation, and the charts
under `reports/charts/` went on drawing the inverted shape with nothing on
them to say so. RFC-056 conceded that gap on the grounds that it was a
one-time migration window; it is not, because the scoring rule will change
again. So a chart now carries three things, in descending order of how much
they survive:

  - a VISIBLE line, in the chart's own notice rows, when the profile it
    draws was scored under a rule this codebase no longer runs. It survives
    a screenshot, which is the only thing that does, and it is the one
    warning that matters — the shape may be inverted;
  - a visible built-at meta line naming the scoring and chart contract
    versions, so a reader can tell WHICH wingman drew this;
  - an XML comment carrying the machine-comparable fingerprint
    (`application.freshness`), which survives copying and emailing but not
    a screenshot, and lets `wingman artifacts stale` judge a file on disk
    without re-deriving it.

Nothing is built for the screenshot case beyond that first line. A picture
that has been photographed is outside what any code here can reach, and
pretending otherwise would mean designing for a promise that cannot be kept.

**A label drawn outside the viewBox is a label nobody reads (issue #354).**
Axis names were placed at a fixed radius and drawn at whatever length they
happened to be, with no width budget anywhere in the geometry. Measured
against a real profile, four of five labels ran past the canvas — the worst
by 204px of a 640px viewBox, a third of the width — and `viewBox` clips, so
that text was gone, not merely tight. It was never going to stay rare: the
model names the axes and RFC-051 asks for DESCRIPTIVE names ("Compassion
for the marginalized and downtrodden", 46 characters, is real output).

So a label now knows how much room it has and takes no more. `_label_budget`
derives the room from the anchor the label already had — a `start`-anchored
label may run to the right edge, an `end`-anchored one to the left, a
`middle`-anchored one symmetrically — and `_wrap_to_budget` breaks the name
across `<tspan>` lines that fit it. Two consequences fall out of deriving
the budget from the anchor: a block can never cross the chart's centre line,
so left and right labels cannot collide with each other, and the wrapped
height is known before anything is drawn, so `render_value_radar_svg` grows
the canvas to contain the tallest one rather than discovering it too late.
The canvas is also wider than it was (640 -> 800), which buys no correctness
at all — wrapping is what guarantees that — but turns a name that would wrap
into five 14-character slivers into one that wraps into two readable lines.

Two things were rejected. Truncating with an ellipsis: an axis name IS the
meaning of its spoke, and a silently cut one is worse than a wrapped one,
because the reader cannot tell that anything was lost. Numbering the spokes
and moving the names to the legend: that trades away the one thing a radar
is for, reading the shape at a glance. Text width is estimated rather than
measured (`_text_width`) — no font metrics without a rendering dependency
AGENTS.md rules out — and the estimate leans deliberately WIDE, because
being wrong toward "wrapped a line early" costs a reader nothing and being
wrong the other way is the bug above, returning.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass
from pathlib import Path

from wingman.application.freshness import artefact_stamp, values_radar_fingerprint
from wingman.application.ingest import IngestError
from wingman.application.pov import CORPUS_PERSON_ID, persona_card_id
from wingman.application.values import (
    VIEW_NOUNS,
    VIEW_TITLES,
    new_captures_since,
    refresh_command,
    refresh_tool,
    scoring_is_current,
    work_grounding_note,
)
from wingman.domain.persona import Persona
from wingman.domain.values import (
    RADAR_CONTRACT_VERSION,
    SCORING_CONTRACT_VERSION,
    ValueAxis,
    ValueProfile,
    ValueView,
)
from wingman.infrastructure.config import Config
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import Storage
from wingman.reporting.design_tokens import DESIGN_TOKENS_CSS
from wingman.reporting.export import _slug

_logger = get_logger("reporting.radar")

# Geometry. Axis count is bounded 3-6 by application.values (MIN_AXES_REQUIRED/
# MAX_AXES) — this module trusts that bound rather than re-validating it.
#
# The width is 800 rather than the original 640 (#354). Widening buys no
# correctness — `_wrap_to_budget` is what keeps a label on the canvas at any
# width — it buys LEGIBILITY: at 640 the worst-placed label on a five-axis
# chart has ~85px to work with, which wraps a descriptive axis name into five
# slivers. 800 gives it ~165px, i.e. two ordinary lines. It stops there
# because the chart is already ~740 tall; wider than this and the picture
# stops being one a reader takes in at a glance, which is the whole point of
# a radar.
_WIDTH = 800.0
_CENTER_X = _WIDTH / 2.0
_CENTER_Y = 300.0
_MAX_RADIUS = 190.0
_LABEL_RADIUS = _MAX_RADIUS + 42.0
_VERTEX_RADIUS = 4.5
_RING_FRACTIONS = (0.25, 0.5, 0.75, 1.0)
_NEUTRAL_FRACTION = 0.5  # score == 0
_CHART_BOTTOM = _CENTER_Y + _MAX_RADIUS + 60.0
_LEGEND_ROW_HEIGHT = 30.0
_LEGEND_TOP_PAD = 20.0

# Axis-label typesetting (#354).
_LABEL_FONT_SIZE = 13.0  # must match .radar-axis-label in _RULES_CSS
_LABEL_LINE_HEIGHT = 16.0
#: How far inside the viewBox edge a label must stop. It absorbs the error in
#: `_text_width`'s estimate as well as being visual breathing room, so it is
#: not merely cosmetic: a label measured a little narrow than it renders still
#: lands on the canvas.
_LABEL_MARGIN = 16.0
#: No label line may sit above the 12 o'clock label's own row. That row is
#: already the closest text to the header, and #248 is what happens when
#: something else is drawn near it: the stale warning at y=72 and this label
#: at y=68 obscured each other on every stale chart. Vertically centring a
#: wrapped block would push the top label's first line up into exactly that
#: gap, so the block is clamped here and grows downward instead.
_LABEL_TOP_LIMIT = _CENTER_Y - _LABEL_RADIUS
#: Clearance between the lowest label line and the legend below it.
_LABEL_BOTTOM_PAD = 24.0

# Legend columns, derived from the width so the extra room reaches the
# legend too: at the original 640 the score column sat at x=220, which a
# 46-character axis name overran. This is a column budget, not a wrap — the
# legend is one fixed-height row per axis, and the notice rows and the
# viewBox height are computed from that. A name longer than ~55 characters
# still crowds the score column; the chart itself now spells such a name out
# in full, which is where the fix that matters landed.
_LEGEND_NAME_X = 24.0
_LEGEND_SCORE_X = _WIDTH - 360.0
_LEGEND_LABEL_X = _LEGEND_SCORE_X + 60.0

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
.wingman-radar .radar-caveat { font-size: 11px; fill: var(--accent-orange); }
.wingman-radar .radar-superseded {
  font-size: 11px; font-weight: 700; fill: var(--accent-orange);
}
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


# Per-character advance widths, as a fraction of the font size, for the
# label face. `--font-sans` resolves to system-ui, so the actual face is the
# READER's — there is no metric this module could look up even if a font
# library were allowed (AGENTS.md's minimal-dependency baseline), and the
# reader's browser will re-measure everything anyway. What can be controlled
# is the DIRECTION of the error, so these sit at the wide end of what those
# faces do: ordinary prose measures ~7.5px per character at 13px, against the
# ~6.2px/char a real chart was measured at. Over-estimating wraps a line
# early, which costs a reader nothing. Under-estimating puts text past the
# viewBox edge, which is #354.
_EM_NARROW = 0.30
_EM_SEMI = 0.42
_EM_WIDE = 0.95
_EM_CAP = 0.72
_EM_DEFAULT = 0.58
_NARROW_CHARS = frozenset(" il.,:;'\"!|()[]{}")
_SEMI_CHARS = frozenset("ftrjI-/\\")
_WIDE_CHARS = frozenset("mwMW@%")


def _char_em(char: str) -> float:
    if char in _NARROW_CHARS:
        return _EM_NARROW
    if char in _SEMI_CHARS:
        return _EM_SEMI
    if char in _WIDE_CHARS:
        return _EM_WIDE
    if char.isupper() or char.isdigit():
        return _EM_CAP
    return _EM_DEFAULT


def _text_width(text: str, font_size: float = _LABEL_FONT_SIZE) -> float:
    """An estimate of the rendered width of `text`, in px, leaning wide —
    see `_EM_*` above for why the lean is the point."""
    return font_size * sum(_char_em(char) for char in text)


def _label_budget(x: float, anchor: str) -> float:
    """How wide a label anchored at `x` may be drawn without leaving the
    canvas. Derived from the anchor the label already had, which is why a
    left-side and a right-side label can never grow into each other: each
    one's room runs from its own anchor to its own edge, and neither crosses
    the centre."""
    if anchor == "start":
        return _WIDTH - x - _LABEL_MARGIN
    if anchor == "end":
        return x - _LABEL_MARGIN
    # Centred: the text grows both ways, so the budget is twice the shorter
    # side — a top label pulled slightly off-centre still cannot overrun.
    return 2.0 * (min(x, _WIDTH - x) - _LABEL_MARGIN)


def _split_to_fit(word: str, budget: float) -> list[str]:
    """A single word too wide for the budget, broken across lines.

    The last resort, and rare — it takes a ~25-character unbroken run. Still
    handled rather than left to overflow: a name that is one long token is
    exactly the input a wrapper that only breaks on spaces gets wrong, and
    the failure mode would be the original bug, back for one input class.
    """
    if _text_width(word) <= budget:
        return [word]
    pieces: list[str] = []
    current = ""
    for char in word:
        if current and _text_width(current + char) > budget:
            pieces.append(current)
            current = char
        else:
            current += char
    if current:
        pieces.append(current)
    return pieces


def _wrap_to_budget(text: str, budget: float) -> list[str]:
    """`text` as lines that each fit `budget`. Greedy, breaking on spaces.

    Never returns an empty list, and never drops a character: everything the
    axis was named is drawn somewhere. Runs of whitespace collapse, which is
    what a reader wants of a model-produced name anyway.
    """
    lines: list[str] = []
    current = ""
    for word in text.split():
        pieces = _split_to_fit(word, budget)
        if len(pieces) > 1:
            # A hard-split word owns its lines outright: gluing the tail of
            # one to the next word ("...trodde n and") reads as a typo.
            if current:
                lines.append(current)
            lines.extend(pieces[:-1])
            current = pieces[-1]
        elif not current:
            current = pieces[0]
        elif _text_width(f"{current} {pieces[0]}") <= budget:
            current = f"{current} {pieces[0]}"
        else:
            lines.append(current)
            current = pieces[0]
    if current:
        lines.append(current)
    return lines or [text]


@dataclass(frozen=True)
class _LabelBlock:
    """One axis name, already wrapped and placed.

    Computed for every axis BEFORE anything is drawn, because the wrapped
    height decides where the legend starts and how tall the viewBox has to
    be — the same "reserve the room you draw in" rule #248 established for
    the notice rows.
    """

    lines: tuple[str, ...]
    x: float
    first_baseline: float
    anchor: str

    @property
    def bottom(self) -> float:
        return self.first_baseline + _LABEL_LINE_HEIGHT * (len(self.lines) - 1)


def _label_blocks(names: list[str]) -> list[_LabelBlock]:
    """Every axis label, wrapped to the room its own position leaves it."""
    count = len(names)
    blocks: list[_LabelBlock] = []
    for index, name in enumerate(names):
        angle = _axis_angle(index, count)
        x, y = _polar(_LABEL_RADIUS, angle)
        anchor = _label_anchor(angle)
        lines = _wrap_to_budget(name, _label_budget(x, anchor))
        # Centred on the spoke's own y, so a wrapped block stays visually
        # attached to the axis it names — except at the top, where centring
        # would push the first line into the header (see _LABEL_TOP_LIMIT).
        centred = y - _LABEL_LINE_HEIGHT * (len(lines) - 1) / 2.0
        blocks.append(
            _LabelBlock(
                lines=tuple(lines),
                x=x,
                first_baseline=max(_LABEL_TOP_LIMIT, centred),
                anchor=anchor,
            )
        )
    return blocks


def _evidence_excerpt(axis: ValueAxis) -> str:
    if not axis.evidence:
        return ""
    first = axis.evidence[0]
    quote = first.quote if len(first.quote) <= 140 else first.quote[:137] + "…"
    return f'"{quote}" — {first.target}'


def _ring_points(count: int, fraction: float) -> str:
    points = [_polar(_MAX_RADIUS * fraction, _axis_angle(i, count)) for i in range(count)]
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


@dataclass(frozen=True)
class _Notice:
    """One warning row under the legend: the text, and the class that styles
    it. A list rather than the single stale line this used to hold, because
    a chart can now be out of date in two independent ways at once and
    neither may silently displace the other."""

    text: str
    css_class: str


def _notices(profile: ValueProfile, stale_new_captures: int) -> list[_Notice]:
    """Every warning this chart has to carry, most serious first.

    The superseded-scoring warning leads because it is the one that says
    the SHAPE may be wrong, where the stale-captures warning only says the
    shape is incomplete. A reader who reads one line reads that one.

    The wording is this module's own rather than
    `application.values.scoring_note`'s, deliberately: that one is a
    parenthetical sized for a terminal, and this one has to fit the canvas
    at 11px on one row, with no wrapping. Both surfaces warning for the same
    profile is what a test asserts; matching prose is not.
    """
    notices: list[_Notice] = []
    rebuild = refresh_command(profile.view)
    if not scoring_is_current(profile):
        headline = (
            "⚠ built before per-item direction was recorded — an axis evidenced by a "
            "'con' may be inverted"
            if any(span.direction is None for axis in profile.axes for span in axis.evidence)
            else (
                f"⚠ scored under {profile.scoring_version or 'an unrecorded rule'}; current is "
                f"{SCORING_CONTRACT_VERSION} — this shape may be superseded"
            )
        )
        notices.append(_Notice(headline, "radar-superseded"))
        notices.append(
            _Notice(f"rebuild with '{rebuild}', then re-export this chart", "radar-superseded")
        )
    # The work view's own caveat (#356), on the chart for the same reason
    # the superseded warning is: an SVG gets emailed away from every
    # surface that would otherwise say the axes rest on character evidence,
    # and a picture is the form in which somebody is most likely to quote
    # one at a hiring conversation. Shortened for a 640px canvas at 11px —
    # `application.values.work_grounding_note` carries the full wording.
    if work_grounding_note(profile):
        notices.append(
            _Notice(
                "⚠ read off nominations about PEOPLE — no perspective reactions among the evidence",
                "radar-caveat",
            )
        )
        notices.append(
            _Notice(
                "capture alignment_of_perspective_agree/_disagree reactions and rebuild to "
                "ground it",
                "radar-caveat",
            )
        )
    if stale_new_captures:
        noun = "capture" if stale_new_captures == 1 else "captures"
        notices.append(
            _Notice(
                f"{stale_new_captures} new {noun} since this was built — rebuild with "
                f"'{rebuild}' to include them",
                "radar-stale",
            )
        )
    return notices


def render_value_radar_svg(profile: ValueProfile, stale_new_captures: int = 0) -> str:
    """A self-contained SVG radar chart of `profile.axes` — see this module's
    docstring for the signed-score-to-radius convention. Never mutates or
    recomputes anything on `profile`; a pure rendering of the stored,
    already-scored contract."""
    axes = profile.axes
    count = len(axes)
    notices = _notices(profile, stale_new_captures)
    # The labels are laid out first because everything below them moves when
    # one of them wraps: a two-line bottom label that the legend was not
    # asked about lands on the legend's first row (#354).
    blocks = _label_blocks([axis.name for axis in axes])
    chart_bottom = max(_CHART_BOTTOM, max(block.bottom for block in blocks) + _LABEL_BOTTOM_PAD)
    legend_top = chart_bottom + _LEGEND_TOP_PAD
    legend_bottom = legend_top + _LEGEND_ROW_HEIGHT * count
    notice_top = legend_bottom + _LEGEND_ROW_HEIGHT * 0.5
    height = legend_bottom + _LEGEND_ROW_HEIGHT * len(notices) + 20.0

    # role="img" tells assistive technology to treat the whole chart as a
    # single image, so everything inside it — axis names, signed scores,
    # labels — stops being reachable. The accessible name alone says only
    # WHOSE chart this is. The <desc> is the text equivalent: the same
    # information the legend carries, in reading order — including the
    # notices, because a warning a screen reader cannot reach is a warning
    # for sighted readers only.
    description = "; ".join(f"{axis.name} {axis.score:+.2f}, {axis.label}" for axis in axes)
    for notice in notices:
        description += f"; {notice.text}"
    desc_id = "radar-desc"
    # The view is named in the heading, not left to the axis wording (#356).
    # Two charts of the same person with differently-worded axes, only one
    # of them labelled, is how somebody quotes the wrong one at an
    # interview — and a chart is the form most likely to be quoted.
    heading = f"{VIEW_TITLES[profile.view]}: {profile.subject_name}"

    parts: list[str] = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WIDTH:.0f} {height:.0f}" '
            f'width="{_WIDTH:.0f}" height="{height:.0f}" class="wingman-radar" role="img" '
            f'aria-label="{_e(heading)} radar chart" '
            f'aria-describedby="{desc_id}">'
        ),
        # The file's own provenance, machine-comparable, so 'wingman
        # artifacts stale' can judge a chart sitting on disk without
        # re-deriving it. A comment because it must not draw: the visible
        # warning below is what a reader needs, and a fingerprint printed
        # on the picture is noise to everyone but a script.
        (
            "<!-- "
            + _e(
                artefact_stamp(profile.subject_id, values_radar_fingerprint(profile), profile.view)
            )
            + " -->"
        ),
        f"<title>{_e(heading)}</title>",
        f'<desc id="{desc_id}">{_e(description)}</desc>',
        f"<style>{RADAR_CSS}</style>",
        f'<text class="radar-title" x="24" y="34">{_e(heading)}</text>',
        # The contract versions ride the meta line unconditionally, next to
        # the provider and model that were already there: a reader deciding
        # whether to trust a chart they were sent needs to know which
        # wingman drew it, and a version that only shows up when something
        # is wrong teaches nobody what right looks like.
        (
            f'<text class="radar-meta" x="24" y="54">'
            f"built from {profile.items_used} captured items · "
            f"{_e(profile.provider)}/{_e(profile.model)} · "
            f"scoring {_e(profile.scoring_version or 'unrecorded')} · "
            f"chart {_e(RADAR_CONTRACT_VERSION)} · "
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
    # or away from the chart depending on which side they fall on, and
    # wrapped to the room that leaves them (#354).
    parts.append('<g class="radar-labels">')
    for axis, block in zip(axes, blocks, strict=True):
        opening = (
            f'<text class="radar-axis-label" data-axis="{_e(axis.name)}" '
            f'x="{block.x:.2f}" y="{block.first_baseline:.2f}" text-anchor="{block.anchor}">'
        )
        if len(block.lines) == 1:
            # A one-line label stays plain text — the ordinary case, and it
            # keeps the name readable as a single node to anything parsing
            # the SVG rather than rendering it.
            parts.append(f"{opening}{_e(block.lines[0])}</text>")
            continue
        spans = "".join(
            f'<tspan x="{block.x:.2f}" dy="{0.0 if number == 0 else _LABEL_LINE_HEIGHT:.2f}">'
            f"{_e(line)}</tspan>"
            for number, line in enumerate(block.lines)
        )
        parts.append(f"{opening}{spans}</text>")
    parts.append("</g>")

    # Legend: the raw signed score and deterministic label as plain text,
    # for the sign this chart's geometry alone can't fully convey.
    parts.append('<g class="radar-legend">')
    for index, axis in enumerate(axes):
        row_y = legend_top + _LEGEND_ROW_HEIGHT * index
        parts.append(
            f'<text class="radar-legend-name" x="{_LEGEND_NAME_X:.0f}" '
            f'y="{row_y:.2f}">{_e(axis.name)}</text>'
            f'<text class="radar-legend-score" x="{_LEGEND_SCORE_X:.0f}" '
            f'y="{row_y:.2f}">{axis.score:+.2f}</text>'
            f'<text class="radar-legend-label" x="{_LEGEND_LABEL_X:.0f}" '
            f'y="{row_y:.2f}">{_e(axis.label)}</text>'
        )
    parts.append("</g>")

    # The warnings sit UNDER the legend, not at y=72 in the header. The
    # first axis label is always at y=68 — _LABEL_RADIUS is fixed and the
    # first spoke always points at 12 o'clock — so a warning at y=72 put two
    # text rows 4px apart and obscured both, on every stale chart at every
    # axis count. Down here they have rows to themselves, one per notice,
    # with `height` reserving exactly that many. (Shifting the whole chart
    # down when stale would keep them near the top, but that means two
    # different geometries to reason about for the sake of a couple of lines.)
    for index, notice in enumerate(notices):
        parts.append(
            f'<text class="{notice.css_class}" x="24" '
            f'y="{notice_top + _LEGEND_ROW_HEIGHT * index:.2f}">{_e(notice.text)}</text>'
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

    `scoring_superseded` is the other half of the same mistake (#355): a
    chart drawn from a profile scored under a rule this codebase no longer
    runs may be showing the wrong SHAPE, not merely an incomplete one, and
    the surfaces have to be able to say so without parsing the SVG back.
    `fingerprint` is what the file was built from, so a caller recording a
    published page can stamp the same value on it.
    """

    path: Path
    stale_new_captures: int
    scoring_superseded: bool = False
    fingerprint: str = ""


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
    view: ValueView = ValueView.CHARACTER,
) -> RadarExport:
    """Render the stored `ValueProfile` (own or, with `persona`, a coached
    Persona's — same scoping `application.values.build_value_profile` uses)
    as an SVG radar chart under `reports/charts/`, returning a `RadarExport`
    carrying both the path and the staleness count the callers report.

    `view` (#356) picks which READING to draw. One chart per view, same
    geometry — the geometry never knew what the axes were named, and
    nothing here changes that, so `RADAR_CONTRACT_VERSION` does not move.

    No model call — this only reads whatever profile is already stored.
    Raises `IngestError`, same class of refusal `application.values` already
    uses for "not enough evidence yet", if no profile has been built for
    this subject: the caller is pointed at 'wingman values --refresh' /
    `my_values(refresh=True)` rather than getting an empty or fabricated
    chart. A stale profile (new captures since it was built) still renders —
    staleness is a warning printed on the chart, not a refusal, matching how
    `wingman values`/`my_values` already treat a stored-but-stale profile.

    The same call for a profile scored under a superseded contract (#355):
    it renders, with a warning, and `scoring_superseded` set so the caller
    can repeat it in the terminal. Refusing would be new behaviour, and the
    wrong new behaviour — somebody asking for the chart of a profile they
    have been told is suspect is usually asking precisely in order to see
    how suspect it is.
    """
    subject_id = persona_card_id(persona.persona_id) if persona is not None else CORPUS_PERSON_ID
    profile = storage.get_value_profile(subject_id, view)
    if profile is None:
        subject = persona.name if persona is not None else "you"
        raise IngestError(
            f"no {VIEW_NOUNS[view]} profile built for {subject} yet — run "
            f"'{refresh_command(view)}' (or {refresh_tool(view)}) first, then try the "
            "chart again."
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
    #
    # The view joins it for the same reason (#356), and the character view
    # keeps the pre-#356 filename: a rename would orphan every chart already
    # on disk and every link anybody has to one, for no gain.
    view_part = "" if view is ValueView.CHARACTER else f"-{view.value}"
    filename = (
        f"{_slug(profile.subject_name)[:60]}-{_slug(subject_id)[:24]}{view_part}-values-radar.svg"
    )
    return RadarExport(
        path=_write_svg(directory, filename, svg),
        stale_new_captures=stale,
        scoring_superseded=not scoring_is_current(profile),
        fingerprint=values_radar_fingerprint(profile),
    )
