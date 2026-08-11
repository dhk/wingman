"""The guard that makes the contract versions mean something (issue #355).

A version somebody has to REMEMBER to bump is worse than no version at
all: it manufactures confident freshness. Every artefact stamped with a
version nobody moved reads as current, and the code that checks the stamp
says so with a straight face — which is exactly the failure #340 was, one
layer up.

So the rule is enforced here instead of documented and hoped for. This
module runs the real scoring and the real geometry over a fixed matrix of
inputs, serializes the results, and compares the digest against the one
recorded for the version currently in force. Change what the code
OUTPUTS and this fails; change a comment, a name, or an implementation
detail that leaves every output identical and it does not.

**Why a behavioural probe and not a hash of the source.** Hashing
`application/values.py` would fail on a reworded docstring (noise, which
trains people to bump the version to shut the test up — and a bumped
version marks every stored profile stale for nothing) and would pass on a
semantic change made in a function it calls (silence, the failure that
matters). Running the functions and hashing the ANSWERS has neither
property.

**What a failure here asks of you.** Decide whether the output change was
intended. If it was, bump the constant in `domain/values.py` and add its
new digest below, keeping the old entry so the trail survives. If it was
not, you have found a scoring regression before it reached a chart, which
is the entire point.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from wingman.application import values
from wingman.domain.profile import (
    EvidenceSpan,
    ItemStatus,
    ProfileItem,
    ProfileItemKind,
    SentimentIntensity,
)
from wingman.domain.provenance import ClaimClassification
from wingman.domain.values import (
    RADAR_CONTRACT_VERSION,
    SCORING_CONTRACT_VERSION,
    AxisDirection,
    ProposedAxisCitation,
    ProposedValueAxis,
    ValueAxisProposal,
)
from wingman.reporting import radar

# One digest per version of the scoring contract, oldest first. Entries are
# never deleted — a removed one erases the evidence that the behaviour ever
# differed, which is the same reasoning docs/RFC.md applies to superseded
# entries.
_SCORING_DIGESTS = {
    "values-scoring-1": "232a26a8c69065e97df8d073b555262c2cca65d29d00e88c31071ace13a9b979",
}

# The geometry contract is versioned separately (see domain/values.py) and
# guarded the same way: a remap of signed score to radius changes what shape
# a reader is asked to believe, without changing a single stored number.
_RADAR_DIGESTS = {
    "values-radar-1": "d4a1f759ea9dd4ff53f2c091fe1d42fbe10a0d3490a65628c5d550c1f52da69d",
}

# A fixed sweep, chosen to straddle every bucket boundary in
# `_label_for_score` (0.6, 0.2, -0.2, -0.6) from both sides, plus the ends
# and the exact zero. A rule change that only moved a boundary would slip
# past a coarser sample.
_SCORE_SWEEP = (
    -1.0,
    -0.75,
    -0.6,
    -0.5999,
    -0.2,
    -0.1999,
    -0.0001,
    0.0,
    0.0001,
    0.1999,
    0.2,
    0.5999,
    0.6,
    0.75,
    1.0,
)


def _item(item_id: str, subtype: str, intensity: SentimentIntensity | None) -> ProfileItem:
    return ProfileItem(
        item_id=item_id,
        kind=ProfileItemKind.INTERVIEW,
        subtype=subtype,
        name=f"{subtype}: Target {item_id}",
        detail=f"quote for {item_id}",
        classification=ClaimClassification.FACT,
        confidence=1.0,
        extracted_by="contract-probe",
        prompt_version="contract-probe",
        evidence=[EvidenceSpan(source_record_id=f"src-{item_id}", quote=f"quote for {item_id}")],
        status=ItemStatus.ACTIVE,
        intensity=intensity,
    )


def _scoring_behaviour() -> list[object]:
    """Every output the scoring contract promises, for a fixed input matrix.

    Deliberately built from the real functions rather than from a table of
    expected values: a table is a second implementation, and the two drift.
    Reached through the module object, never through names imported at the
    top of this file, so a monkeypatched implementation is actually the one
    that runs (see the #340 simulation below).
    """
    probe: list[object] = []

    # 1. One item's contribution: magnitude from intensity, sign from
    #    direction. This is the exact cell #340 got wrong — a `_con`
    #    nomination supporting an axis must weigh POSITIVE.
    intensities: list[SentimentIntensity | None] = [None, *values._INTENSITY_MAGNITUDE]
    for subtype in ("values_pro", "values_con", "mission_alignment_con"):
        for intensity in intensities:
            for direction in AxisDirection:
                probe.append(
                    [
                        "signed_weight",
                        subtype,
                        intensity.value if intensity else None,
                        direction.value,
                        round(values._signed_weight(_item("i", subtype, intensity), direction), 12),
                    ]
                )

    # 2. The score -> prose bucket, at and around every boundary.
    probe.extend(["label", score, values._label_for_score(score)] for score in _SCORE_SWEEP)

    # 3. The whole validate-and-aggregate path: which citations survive,
    #    what each weighs, and the mean they produce. Covers the dropping
    #    rules (unknown item, unrecognized direction, duplicate citation)
    #    as well as the arithmetic, because all of them change the number.
    eligible = {
        "a": _item("a", "values_pro", SentimentIntensity.STRONG),
        "b": _item("b", "values_con", SentimentIntensity.STRONG),
        "c": _item("c", "mission_alignment_pro", SentimentIntensity.MILD),
        "d": _item("d", "values_fallback_con", None),
    }
    proposal = ValueAxisProposal(
        axes=[
            ProposedValueAxis(
                name="Condemnation is evidence of the value",
                description="Both halves of the interview argue for it.",
                items=[
                    ProposedAxisCitation(item_id="a", direction="supports"),
                    ProposedAxisCitation(item_id="b", direction="supports"),
                ],
            ),
            ProposedValueAxis(
                name="Mixed, and unevenly weighted",
                items=[
                    ProposedAxisCitation(item_id="c", direction="supports"),
                    ProposedAxisCitation(item_id="b", direction="opposes"),
                    ProposedAxisCitation(item_id="d", direction="opposes"),
                ],
            ),
            ProposedValueAxis(
                name="Unusable citations dropped, not defaulted",
                items=[
                    ProposedAxisCitation(item_id="a", direction="not-a-direction"),
                    ProposedAxisCitation(item_id="a", direction="opposes"),
                    ProposedAxisCitation(item_id="never-supplied", direction="supports"),
                    ProposedAxisCitation(item_id="c", direction=None),
                    ProposedAxisCitation(item_id="c", direction=" SUPPORTS "),
                ],
            ),
            ProposedValueAxis(
                name="Nothing usable at all",
                items=[ProposedAxisCitation(item_id="ghost", direction="supports")],
            ),
        ]
    )
    axes, rejected = values._validate_proposal(proposal, eligible)
    for axis in axes:
        probe.append(
            [
                "axis",
                axis.name,
                round(axis.score, 12),
                axis.label,
                [
                    [span.item_id, span.direction, round(span.signed_weight, 12)]
                    for span in axis.evidence
                ],
            ]
        )
    probe.extend(["rejected", item.name, item.reason] for item in rejected)
    return probe


def _radar_behaviour() -> list[object]:
    """The signed-score-to-radius remap, over the same sweep plus the
    out-of-range clamps a stored profile could in principle carry."""
    return [
        ["radius", score, round(radar._radius_fraction(score), 12)]
        for score in (-2.0, *_SCORE_SWEEP, 2.0)
    ]


def _digest(probe: list[object]) -> str:
    return hashlib.sha256(
        json.dumps(probe, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _assert_guarded(kind: str, version: str, digests: dict[str, str], probe: list[object]) -> None:
    assert version in digests, (
        f"{kind} contract version {version!r} has no recorded behaviour digest. "
        f"If you bumped it deliberately, add {version!r}: {_digest(probe)!r} to this "
        "module's table and keep the previous entry."
    )
    assert _digest(probe) == digests[version], (
        f"{kind} behaviour changed but {kind} contract version {version!r} did not move.\n\n"
        "Every artefact stamped with that version now claims to have been produced by a "
        "rule that no longer exists, and 'wingman artifacts stale' will report those "
        "artefacts as current. That is the #340 failure with a freshness check on top.\n\n"
        f"If the change was intended: bump it in src/wingman/domain/values.py, then add "
        f"the new version with digest {_digest(probe)!r} to this module's table (keep the "
        "old entry). If it was not intended, you have found a scoring regression before "
        "it reached a chart."
    )


def test_scoring_behaviour_cannot_change_without_the_version_moving() -> None:
    _assert_guarded("scoring", SCORING_CONTRACT_VERSION, _SCORING_DIGESTS, _scoring_behaviour())


def test_chart_geometry_cannot_change_without_the_version_moving() -> None:
    _assert_guarded("radar", RADAR_CONTRACT_VERSION, _RADAR_DIGESTS, _radar_behaviour())


def test_the_probe_actually_catches_the_bug_that_motivated_this() -> None:
    """The guard is only worth having if it fires on #340's shape.

    #340 read the sign off the `_pro`/`_con` suffix rather than off the
    model's per-item direction, so a condemnation of somebody who violated
    a value scored NEGATIVE on an axis named for that value. Simulated
    here, the probe must produce a different digest — if it did not, the
    test above would have watched that change go by.
    """
    honest = _scoring_behaviour()

    def _sign_from_subtype(item: ProfileItem, direction: AxisDirection) -> float:
        magnitude = (
            values._INTENSITY_MAGNITUDE.get(item.intensity, 2 / 3) if item.intensity else 2 / 3
        )
        return magnitude * (-1.0 if (item.subtype or "").endswith("_con") else 1.0)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("wingman.application.values._signed_weight", _sign_from_subtype)
        inverted = _scoring_behaviour()

    assert _digest(inverted) != _digest(honest)


def test_every_recorded_version_is_distinct() -> None:
    """Two versions with the same digest mean one of them was bumped for a
    change that altered nothing — which spends every stored artefact's
    freshness for no reason. Cheap to check, and it keeps the table honest.
    """
    for table in (_SCORING_DIGESTS, _RADAR_DIGESTS):
        assert len(set(table.values())) == len(table)
