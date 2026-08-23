"""Rubrics and gap maps: measuring a profile against a named external
standard (docs/QUESTION-BLOCKS-DESIGN.md, issue #436, v0 slice).

A **rubric** is a named external standard — an engineering ladder, a
competency matrix — expressed as dimensions with rungs. It is the RULER.
It is never a measurement, and it never becomes evidence about the person:
nothing here is ever written to `ProfileItem`, `career.md`, or any other
profile surface. Rubrics ship as data files (`wingman/rubrics/*.toml`), so
adding a second standard is adding a file, not editing code — the same
packaged-data pattern `wingman/prompts/*.md` already uses.

A **gap map** is v0's only output: for each of a rubric's dimensions, what
evidence this workspace already holds, and — the point of the exercise —
which dimensions hold none. It deliberately does NOT position the person
on a rung. Q2 in the design doc is open, and the audit that motivated this
slice (§3) found three of five dimensions with nothing to read: a rung
emitted from that would be inventing most of itself.

**No model call anywhere in this slice.** Matching is deterministic —
a dimension's own declared `signals` against profile-item text and the
corpus full-text index. That makes every line of a gap map inspectable
and reproducible, and it means the report can be trusted to say "there is
nothing here" rather than having a model fill the silence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

#: The version of the DETERMINISTIC rule that turns matched evidence into a
#: `Coverage` verdict — the signal-matching, the first-party/third-party
#: split, and the count thresholds in `application.gap_map`. Bump it
#: whenever OUTPUT SEMANTICS change: the same workspace and the same rubric
#: would now produce a different verdict for some dimension. Do not bump it
#: for a refactor or a comment.
#:
#: Same reasoning as `domain.values.SCORING_CONTRACT_VERSION` (RFC-063): a
#: gap map is an artefact somebody may act on weeks later, and "the code
#: that shaped this has since moved" has to be answerable without
#: re-deriving it.
GAP_MAP_CONTRACT_VERSION = "gap-map-1"

#: The honest answer for a rubric whose content may not be redistributed:
#: link to it, do not reproduce it. Never a synonym for "probably fine".
UNSPECIFIED_LICENSE = "unspecified"


class ProvenanceTier(StrEnum):
    """How directly a rubric's content comes from the standard it names —
    the honesty requirement in Q1 of the design doc, made a required field
    rather than a convention.

    FIRST_PARTY: published by the organisation whose standard it is, or
    quoted from a job posting's own levelling language.
    RECONSTRUCTION: assembled from public secondary sources — write-ups,
    aggregators, other companies' published frameworks. NOT the
    organisation's own document, and a rubric at this tier must not be
    presented as if it were.
    INFERRED: derived rather than sourced. Nothing ships at this tier
    today; it exists so a workspace-local rubric someone writes for
    themselves has an honest label instead of borrowing a stronger one.
    """

    FIRST_PARTY = "first_party"
    RECONSTRUCTION = "reconstruction"
    INFERRED = "inferred"


class EvidenceVoice(StrEnum):
    """WHO is vouching for a piece of evidence.

    FIRST_PARTY: the person's own record — an achievement, a role, their
    own writing in the corpus. Something with an artifact or a first-person
    account behind it.
    THIRD_PARTY: somebody else vouching for them — a testimonial, a
    recommendation. Real evidence, and citable, but unfalsifiable praise
    that must not be weighed like a shipped artifact. A dimension carried
    ONLY by third-party evidence is reported as exactly that
    (`Coverage.THIRD_PARTY_ONLY`), because "three people say you are a
    great mentor" and "you changed how two teams ship" are not the same
    claim.

    **Why this is not `relationship.EvidenceTier`.** The design doc (§6)
    proposed reusing RFC-073's OBSERVED/ENDORSED here, and that was wrong
    on semantics: those tiers describe HOW A NOTE WAS PRODUCED — the user's
    own words versus a model-drafted synthesis they confirmed — which is
    orthogonal to who is vouching. A testimonial is verbatim (so OBSERVED
    under RFC-073) while being the third-party praise this enum exists to
    hold at arm's length. RFC-073 itself rejected reusing
    `ClaimClassification` for the same reason, and borrowing its enum here
    would repeat the mistake in the other direction.
    """

    FIRST_PARTY = "first_party"
    THIRD_PARTY = "third_party"


class Coverage(StrEnum):
    """What a gap map concluded about one dimension. Deterministic — see
    `application.gap_map` for the thresholds and
    `GAP_MAP_CONTRACT_VERSION` for their version.

    EVIDENCED: enough first-party evidence to be worth reading.
    THIN: some first-party evidence, but little.
    THIRD_PARTY_ONLY: evidence exists and every piece of it is somebody
    else vouching. Called out separately rather than folded into THIN,
    because the fix is different: THIN wants more of the same, this wants
    a different kind.
    ABSENT: nothing matched at all. This is the finding v0 exists to
    produce, and it is a real answer, not a failure.
    """

    EVIDENCED = "evidenced"
    THIN = "thin"
    THIRD_PARTY_ONLY = "third_party_only"
    ABSENT = "absent"


class RubricRung(BaseModel):
    """One step on a dimension. Carried for v1/v2; **v0 never reads
    these** — positioning a person on a rung is exactly what this slice
    declines to do (Q2). They live in the rubric file now so that the file
    format does not have to change when the question is settled.
    """

    level: str = Field(min_length=1)
    descriptor: str = Field(min_length=1)


class RubricDimension(BaseModel):
    """One axis a rubric measures, and the terms that find evidence for it.

    `signals` are the deterministic matcher's whole input: lowercase words
    and phrases that, appearing in a profile item or a corpus document,
    make that document a candidate for this dimension. They are declared in
    the rubric file rather than computed, so a reader can see exactly why
    something was matched and can fix a bad match by editing data.

    `probe` is the question to ask when this dimension has no evidence —
    v1's question script, authored here because the gap IS the question.
    v0 reports it as the suggested next step and captures nothing.

    `confusable_with` names a signal that LOOKS like this dimension and is
    not. The audit behind this slice found one that matters: outcome
    magnitude ("$100B+ in ledger activity") reads as organisational scope
    but is the size of a system, which one person can build alone. A
    reading that does not hold those apart systematically over-positions
    strong individual contributors, so the warning travels with the
    dimension rather than living in a doc nobody reads at the time.
    """

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    asks: str = Field(min_length=1)
    signals: list[str] = Field(min_length=1)
    probe: str = ""
    confusable_with: str = ""
    rungs: list[RubricRung] = Field(default_factory=list)


class RubricProvenance(BaseModel):
    """Where a rubric's content came from, what it may claim to be, and
    under what terms its text may be carried.

    `disclaimer` is required and non-empty on purpose: every surface that
    renders a gap map renders this line with it, so a reconstruction can
    never be read as the organisation's own document just because nobody
    thought to mention it (invariant 9, "partial truth over polished
    fiction").

    `license` is required for the same reason, and it is the field that
    stops "publicly readable" from silently becoming "ours to
    redistribute". A rubric that carries an organisation's own descriptor
    text is carrying somebody else's copyrighted work, and the terms have
    to travel with it. Use the SPDX identifier where there is one
    (`Apache-2.0`, `CC-BY-4.0`, `CC-BY-SA-4.0`), or the literal string
    `unspecified` — which is an honest answer meaning *link to it, do not
    reproduce it*, never a synonym for "probably fine".

    **Verify a licence at the primary source, never from a catalogue.**
    A public framework catalogue listed Dropbox's framework as having no
    licence when the Dropbox-owned repository it came from is Apache-2.0.
    Catalogue metadata that is wrong about a case you can check should not
    be trusted for the ones you cannot.
    """

    tier: ProvenanceTier
    sources: list[str] = Field(default_factory=list)
    disclaimer: str = Field(min_length=1)
    license: str = Field(min_length=1)
    #: Path, relative to the packaged `wingman/rubrics/` directory, of the
    #: retained licence text for content this rubric carries. Required
    #: whenever `license` is anything but `unspecified` — see
    #: `_licence_text_is_retained`.
    license_file: str = ""
    #: Attribution notice to retain verbatim, e.g. "Copyright (c) 2021
    #: Dropbox, Inc." Most licences that permit redistribution require this,
    #: and it is the line a reader needs in order to know whose words these
    #: are.
    attribution: str = ""

    @model_validator(mode="after")
    def _licence_text_is_retained(self) -> RubricProvenance:
        """A licence that permits redistribution also imposes conditions —
        Apache-2.0 wants the licence text and the notices carried along,
        CC BY wants attribution. Naming a licence while shipping neither is
        the failure this guards: it looks compliant in the metadata and is
        not compliant on disk.

        `unspecified` is exempt because it is a declaration that nothing is
        being redistributed in the first place.
        """
        if self.license.strip().lower() == UNSPECIFIED_LICENSE:
            return self
        if not self.license_file.strip():
            raise ValueError(
                f"license {self.license!r} permits redistribution, so license_file must "
                "name the retained licence text (or declare 'unspecified' and link instead)."
            )
        if not self.attribution.strip():
            raise ValueError(
                f"license {self.license!r} requires attribution, so attribution must carry "
                "the copyright notice verbatim."
            )
        return self


class Rubric(BaseModel):
    """A named external standard, loaded from a packaged or workspace file."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    version: str = Field(min_length=1)
    provenance: RubricProvenance
    dimensions: list[RubricDimension] = Field(min_length=1)


class DimensionEvidence(BaseModel):
    """One matched piece of evidence, kept citable.

    `source` is what a reader can go and look at: a profile item's id, or a
    corpus document's title. `matched` names which of the dimension's own
    signals fired, so a wrong match is diagnosable from the report itself
    rather than by re-running the matcher.
    """

    source: str
    kind: str
    voice: EvidenceVoice
    excerpt: str
    matched: list[str] = Field(default_factory=list)


class DimensionGap(BaseModel):
    """One dimension's standing in this workspace."""

    dimension_id: str
    name: str
    asks: str
    coverage: Coverage
    probe: str = ""
    confusable_with: str = ""
    first_party: int = 0
    third_party: int = 0
    evidence: list[DimensionEvidence] = Field(default_factory=list)


class GapMap(BaseModel):
    """v0's whole output: a rubric, and what this workspace can and cannot
    answer against it.

    There is no rung, no score, and no overall verdict — by design, not by
    omission. `contract_version` records the rule that produced the
    coverage verdicts, so a map read later can be recognised as computed
    under a rule the code has since replaced.
    """

    rubric_id: str
    rubric_title: str
    rubric_version: str
    provenance: RubricProvenance
    dimensions: list[DimensionGap] = Field(default_factory=list)
    items_read: int = 0
    contract_version: str = GAP_MAP_CONTRACT_VERSION
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
