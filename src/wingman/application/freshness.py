"""Which derived artefacts have stopped being true, and what rebuilds them
(issue #355, docs/RFC.md RFC-063).

A derived artefact — a values radar, a profile export — is a snapshot of
two things that both move: the INPUTS it was built from, and the CODE that
shaped them. Only the first was ever answerable. `new_captures_since`
(RFC-051) diffs a stored `ValueProfile`'s `source_item_ids` against the
currently-eligible captures, so a stale profile says "N new captures since
this was built". Nothing said anything at all about the second, and #340
is what that costs: a change to the scoring rule inverted the sign of
every axis evidenced by a condemnation, and every profile and every
exported chart built beforehand went on displaying the exact opposite of
the truth. A file sitting in `reports/charts/` gave no hint.

This module answers both, for the artefacts that can be asked, and names
the one command that rebuilds each. It is deliberately the ONLY module
that interprets a fingerprint.

**Why not in `application.artifacts`.** RFC-061 stores
`PublishedArtifact.built_from` opaquely and refuses to read it, because a
recording module that interprets it owns a second opinion about staleness
that can drift from the producing view's own. That reasoning is kept: the
knowledge of what makes a values radar stale lives here, next to the view
that produces it, and `application.artifacts` still stores a string it
never looks inside. What changed is that somebody now looks — one module,
named, rather than the recorder quietly growing an opinion.

**Comparison is by equality, never by parsing.** A fingerprint is built by
the producing view and compared against a freshly-computed one for the
same kind. Nothing splits it on a delimiter or reads a field out of it, so
its format can change without a reader breaking; a fingerprint from an
older format simply compares unequal, which is the honest answer — the
artefact was built by a wingman this one cannot vouch for.

**Warn, never refuse.** Nothing here blocks anything. It reports, and
every report carries the command that fixes it. That is the precedent
RFC-015 (stale snapshots), RFC-056 (the render warning) and the changelog
note already set, and the alternative is worse in the exact case that
matters: refusing to draw a superseded chart takes away the only view of
the evidence at the moment somebody is trying to work out whether to
trust it.

**Scope, stated (issue #355).** Only `values_radar` is checked. It is the
artefact that actually broke, the only one with a stored derivation to
compare against, and the only one whose scoring is versioned. The other
two `ARTIFACT_KINDS` — `completeness` and `profile` — are rendered live
from the workspace every time they are produced, so there is no stored
derivation for "is this current" to mean anything against; the honest
answer for a published one is its age, which the listing already prints.
`stale_artefacts` says so out loud rather than returning a clean bill of
health that only covers a third of the kinds.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from wingman.application.pov import CORPUS_PERSON_ID
from wingman.application.values import new_captures_since, scoring_is_current
from wingman.domain.artifacts import ARTIFACT_KINDS
from wingman.domain.values import RADAR_CONTRACT_VERSION, ValueProfile
from wingman.infrastructure.config import Config
from wingman.infrastructure.storage import Storage

#: Kinds this module can actually answer for. Everything else in
#: `ARTIFACT_KINDS` is rendered live and has no stored derivation to compare
#: against — see the module docstring.
CHECKED_KINDS = ("values_radar",)

#: How an exported file carries its own provenance. An XML comment survives
#: being copied, emailed, or committed; it does not survive a screenshot,
#: and nothing here pretends otherwise (see `reporting.radar`).
#:
#: Two markers, not one. `subject` says WHOSE artefact the file is, so a
#: coached persona's chart is never reported stale against the owner's
#: profile — a false alarm is what teaches people to ignore a warning. The
#: fingerprint itself stays opaque: this reads the marker out of the
#: comment, never a field out of the fingerprint.
FINGERPRINT_MARKER = "wingman-artefact"
_STAMP_RE = re.compile(
    rf"{FINGERPRINT_MARKER} subject=(?P<subject>\S+) fingerprint=(?P<fingerprint>\S+)"
)


class ArtefactStaleness(BaseModel):
    """One artefact, and every reason it may no longer be true.

    `reasons` empty means checked and current. It never means "unknown" —
    an artefact this module cannot judge is reported with `checked=False`
    and a reason saying so, because a silent omission from a staleness
    report reads as a clean bill of health.
    """

    kind: str
    subject: str
    checked: bool = True
    reasons: list[str] = Field(default_factory=list)
    rebuild_command: str = ""
    rebuild_tool: str = ""

    @property
    def stale(self) -> bool:
        return bool(self.reasons)


def values_radar_fingerprint(profile: ValueProfile) -> str:
    """What a values radar was built from, as one comparable string.

    Three things decide whether a drawn chart still says what the workspace
    says: WHICH profile it drew (a rebuild mints a new `profile_id`, so a
    chart of the old one is a chart of superseded numbers), the scoring
    contract those numbers were computed under, and the geometry contract
    that turned them into a shape. Change any one and the picture asserts
    something the workspace no longer does.

    Human-readable on purpose. It is printed in listings and embedded in
    exported files, so somebody comparing two by eye should be able to see
    WHICH part moved — but no code reads a part out of it (see the module
    docstring on equality). Semicolons rather than spaces so it survives
    being embedded as one token in a file stamp.
    """
    return (
        f"profile={profile.profile_id[:12]};"
        f"scoring={profile.scoring_version or 'unrecorded'};"
        f"radar={RADAR_CONTRACT_VERSION}"
    )


def artefact_stamp(subject_id: str, fingerprint: str) -> str:
    """The provenance line a produced FILE carries, so it can still answer
    for itself once it has left the workspace.

    Built here, next to the reader, so the two cannot drift apart.
    """
    return f"{FINGERPRINT_MARKER} subject={subject_id} fingerprint={fingerprint}"


def current_fingerprint(kind: str, storage: Storage) -> str:
    """The fingerprint a view of `kind` built from the workspace RIGHT NOW
    would carry — "" for a kind with no stored derivation, or when the
    derivation does not exist yet.

    This is what `artifacts remember` stamps onto a recorded url, so a
    later run can tell a page built from today's profile from one built
    from the profile before it.
    """
    if kind != "values_radar":
        return ""
    profile = storage.get_value_profile(CORPUS_PERSON_ID)
    return values_radar_fingerprint(profile) if profile is not None else ""


def stamp_in(text: str) -> tuple[str, str]:
    """`(subject_id, fingerprint)` from a produced file's stamp, or `("", "")`
    if it carries none.

    A file exported before this existed has no stamp, and that is reported
    as "cannot tell" rather than as "fine" — the whole failure this module
    addresses is an artefact that looked fine.
    """
    found = _STAMP_RE.search(text)
    return (found.group("subject"), found.group("fingerprint")) if found else ("", "")


def _exported_chart_reasons(config: Config, subject_id: str, expected: str) -> list[str]:
    """Reasons drawn from chart files already written to disk.

    `reports/charts/` is exactly where the #340 charts sat, looking
    authoritative, for hours. A file whose embedded fingerprint differs
    from the current one was drawn from something the workspace has since
    replaced; a file with no stamp at all predates the stamp entirely,
    which puts it in the window where the signs may be inverted.

    Files stamped for a DIFFERENT subject — a coached persona's chart —
    are skipped, not judged: they answer to their own profile, and calling
    one stale against this one would be a false alarm.
    """
    directory = config.reports_dir / "charts"
    if not directory.is_dir():
        return []
    reasons: list[str] = []
    for path in sorted(directory.glob("*-values-radar.svg")):
        try:
            found_subject, found = stamp_in(path.read_text(encoding="utf-8"))
        except OSError:
            # An unreadable file is not a staleness verdict, and refusing
            # the whole report over one of them would be worse than the
            # gap: the other charts still have something true to say.
            continue
        if found_subject and found_subject != subject_id:
            continue
        if found == expected:
            continue
        detail = f"was drawn from {found}" if found else "carries no provenance stamp"
        reasons.append(f"the exported chart {path.name} {detail}, not {expected}")
    return reasons


def _values_radar_staleness(config: Config, storage: Storage) -> ArtefactStaleness:
    """The owner's OWN value radar — never a coached persona's.

    `published_artifacts` holds one row per kind for the whole workspace
    (RFC-061), and a persona's chart is a different subject's artefact that
    happens to be produced by the same code. Judging both against one
    record would report one of them wrong every time. Persona charts on
    disk are skipped by subject; a persona page is not published here.
    """
    report = ArtefactStaleness(
        kind="values_radar",
        subject="your value profile",
        rebuild_command="wingman values --refresh && wingman values-chart",
        rebuild_tool="my_values(refresh=True) then values_chart()",
    )
    profile = storage.get_value_profile(CORPUS_PERSON_ID)
    if profile is None:
        report.checked = False
        report.reasons.append(
            "no value profile has been built yet, so there is nothing to be stale"
        )
        return report

    new_captures = new_captures_since(storage, profile)
    if new_captures:
        noun = "capture" if new_captures == 1 else "captures"
        report.reasons.append(f"{new_captures} new {noun} since this profile was built")
    if not scoring_is_current(profile):
        built_under = profile.scoring_version or "an unrecorded scoring rule"
        report.reasons.append(
            f"scored under {built_under}, which this version of wingman no longer runs"
        )

    expected = values_radar_fingerprint(profile)
    published = storage.get_published_artifact("values_radar")
    if published is not None and published.built_from != expected:
        detail = (
            f"was built from {published.built_from}"
            if published.built_from
            else "recorded nothing about what it was built from"
        )
        report.reasons.append(f"the published page ({published.url}) {detail}, not {expected}")
    report.reasons.extend(_exported_chart_reasons(config, CORPUS_PERSON_ID, expected))
    return report


def stale_artefacts(config: Config, storage: Storage) -> list[ArtefactStaleness]:
    """Every artefact this workspace can judge, stale or not.

    Returns the current ones too. A report listing only problems cannot be
    distinguished from a report that failed to look.
    """
    return [_values_radar_staleness(config, storage)]


def render_staleness(reports: list[ArtefactStaleness]) -> str:
    """The listing, including what was NOT examined.

    The unchecked kinds are worked out here rather than passed in, so a
    caller cannot omit them: a staleness report that quietly covers a third
    of the artefact kinds is the confident-freshness failure this whole
    mechanism exists to prevent, arriving by a different route, and that is
    not something to leave to a surface remembering to ask.
    """
    not_checked = [kind for kind in ARTIFACT_KINDS if kind not in CHECKED_KINDS]
    lines = ["Artefact freshness — inputs AND the code that shaped them:"]
    for report in reports:
        if not report.checked:
            lines.append(f"- {report.kind} ({report.subject}): {'; '.join(report.reasons)}")
            continue
        if not report.stale:
            lines.append(
                f"- {report.kind} ({report.subject}): current — built from every eligible "
                "capture, under the current scoring and chart contracts."
            )
            continue
        lines.append(f"- {report.kind} ({report.subject}): STALE")
        lines.extend(f"    · {reason}" for reason in report.reasons)
        lines.append(f"    rebuild: {report.rebuild_command}")
        lines.append(f"    or, in a conversation: {report.rebuild_tool}")
    if not_checked:
        lines.append("")
        lines.append(
            "Not checked: "
            + ", ".join(not_checked)
            + ". These are rendered live from the workspace each time, so there is no stored "
            "derivation to compare against — a published one is a snapshot whose age is the "
            "only honest thing to say about it ('wingman artifacts list' prints it)."
        )
    return "\n".join(lines)


__all__ = [
    "CHECKED_KINDS",
    "FINGERPRINT_MARKER",
    "ArtefactStaleness",
    "artefact_stamp",
    "current_fingerprint",
    "render_staleness",
    "stale_artefacts",
    "stamp_in",
    "values_radar_fingerprint",
]
