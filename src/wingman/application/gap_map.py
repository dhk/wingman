"""The gap map: what a workspace can and cannot answer against a rubric
(docs/QUESTION-BLOCKS-DESIGN.md §7 "v0", issue #436).

Reads the profile and the corpus against a rubric's dimensions and reports,
per dimension, what evidence exists and — the point — what does not.

**Three things this deliberately does not do.**

1. **No rung, no score, no overall verdict.** Q2 of the design doc is open,
   and the audit that motivated the slice found three of five dimensions
   with nothing to read. A position derived from that would be mostly
   invention. `Coverage` says how well evidenced a dimension is, never how
   good the person is.
2. **No model call.** Matching is a dimension's own declared `signals`
   against text, so every line of the output is reproducible and a wrong
   match is fixable by editing a data file. It also means "nothing here"
   is a real finding rather than a silence a model felt obliged to fill.
3. **No writes.** Nothing computed here becomes a `ProfileItem` or reaches
   `career.md`. A rubric is the ruler; measuring with it must not change
   the thing being measured.

Interview captures are excluded from the read entirely. Every interview
subtype records a judgment about somebody ELSE — a person nominated, an
organisation, an article reacted to — so matching a ladder signal inside
one would credit the person for someone else's scope.
"""

from __future__ import annotations

from wingman.domain.profile import ItemStatus, ProfileItem, ProfileItemKind
from wingman.domain.rubric import (
    Coverage,
    DimensionEvidence,
    DimensionGap,
    EvidenceVoice,
    GapMap,
    Rubric,
    RubricDimension,
)
from wingman.infrastructure.logs import get_logger
from wingman.infrastructure.storage import CorpusSearchError, Storage

_logger = get_logger("application.gap_map")

#: First-party evidence needed before a dimension reads as EVIDENCED rather
#: than THIN. Three, because one match is a coincidence and two is a pair;
#: it is a judgment call, and it is versioned by
#: `domain.rubric.GAP_MAP_CONTRACT_VERSION` precisely because it is one.
EVIDENCED_THRESHOLD = 3

#: Most citations kept per dimension. The report is meant to be read, and a
#: dimension matching sixty documents proves its point in five. The COUNTS
#: are never truncated — only the listed citations — so a reader is never
#: shown a smaller number than the matcher actually found.
MAX_CITATIONS = 5

#: Profile kinds a ladder may read. INTERVIEW is excluded by design (see the
#: module docstring); everything else is a first-person career record.
READABLE_KINDS = frozenset(
    {
        ProfileItemKind.ACHIEVEMENT,
        ProfileItemKind.ROLE,
        ProfileItemKind.SKILL,
        ProfileItemKind.TESTIMONIAL,
    }
)


def _voice(kind: ProfileItemKind) -> EvidenceVoice:
    """Who is vouching. A testimonial is somebody else's word for it; every
    other readable kind is the person's own record."""
    if kind is ProfileItemKind.TESTIMONIAL:
        return EvidenceVoice.THIRD_PARTY
    return EvidenceVoice.FIRST_PARTY


def _matched_signals(text: str, signals: list[str]) -> list[str]:
    """Which of `signals` appear in `text`, in the rubric's own order.

    Substring matching, deliberately: signals are authored as fragments
    ('optimiz', 'cross-team') precisely so one entry covers a family of
    words, and a tokenizer would defeat that. The cost is that a signal can
    fire inside a longer word; the mitigation is that every match is
    reported with the signal that caused it, so a bad one is visible in the
    output and fixable in the data file.
    """
    lowered = text.lower()
    return [signal for signal in signals if signal in lowered]


def _fts_query(signals: list[str]) -> str:
    """One FTS5 query matching any of a dimension's signals.

    A multi-word or hyphenated signal becomes a quoted phrase; a bare token
    becomes a prefix term, so 'optimiz' finds 'optimized' and 'optimising'
    the way the substring rule does for profile text. Without the prefix the
    two halves of this matcher would disagree about what a signal means.
    """
    terms: list[str] = []
    for signal in signals:
        cleaned = signal.replace('"', " ").strip()
        if not cleaned:
            continue
        if " " in cleaned or "-" in cleaned:
            terms.append(f'"{cleaned}"')
        else:
            terms.append(f"{cleaned}*")
    return " OR ".join(terms)


def _profile_evidence(
    items: list[ProfileItem], dimension: RubricDimension
) -> list[DimensionEvidence]:
    """Every readable profile item whose text carries one of the dimension's signals."""
    found: list[DimensionEvidence] = []
    for item in items:
        text = f"{item.name} {item.detail}"
        matched = _matched_signals(text, dimension.signals)
        if not matched:
            continue
        found.append(
            DimensionEvidence(
                source=item.item_id,
                kind=item.kind.value,
                voice=_voice(item.kind),
                excerpt=item.name,
                matched=matched,
            )
        )
    return found


def _corpus_evidence(
    storage: Storage, dimension: RubricDimension, limit: int
) -> list[DimensionEvidence]:
    """Corpus documents matching the dimension, as first-party evidence.

    The corpus is the person's own writing, so every hit is first-party.
    `matched` is computed from the returned excerpt rather than the whole
    document, because the corpus body lives only in the full-text index —
    so it reports the signals a reader can actually SEE in the citation,
    which is the honest claim to make about it.
    """
    query = _fts_query(dimension.signals)
    if not query:
        return []
    try:
        hits = storage.search_corpus(query, limit=limit)
    except CorpusSearchError as exc:
        # A rubric is data, and a signal list can be edited into something
        # FTS cannot parse. One unparseable dimension must cost that
        # dimension, not the whole report.
        _logger.warning("gap_map_corpus_query_failed dimension=%s error=%s", dimension.id, exc)
        return []
    found: list[DimensionEvidence] = []
    for document, snippet in hits:
        excerpt = snippet.replace("[", "").replace("]", "").strip()
        found.append(
            DimensionEvidence(
                source=document.title,
                kind="corpus",
                voice=EvidenceVoice.FIRST_PARTY,
                excerpt=excerpt,
                matched=_matched_signals(f"{document.title} {excerpt}", dimension.signals),
            )
        )
    return found


def _coverage(first_party: int, third_party: int) -> Coverage:
    """The deterministic verdict. Versioned by GAP_MAP_CONTRACT_VERSION."""
    if first_party >= EVIDENCED_THRESHOLD:
        return Coverage.EVIDENCED
    if first_party > 0:
        return Coverage.THIN
    if third_party > 0:
        return Coverage.THIRD_PARTY_ONLY
    return Coverage.ABSENT


def build_gap_map(rubric: Rubric, storage: Storage) -> GapMap:
    """Read this workspace against `rubric` and report the gaps.

    Scoped to the coach's own items (`persona_id is None`), matching every
    other profile read in this codebase — a persona's evidence and the
    coach's own never mix.
    """
    items = [
        item
        for item in storage.list_profile_items()
        if item.kind in READABLE_KINDS
        and item.status is ItemStatus.ACTIVE
        and item.persona_id is None
    ]

    dimensions: list[DimensionGap] = []
    for dimension in rubric.dimensions:
        evidence = _profile_evidence(items, dimension) + _corpus_evidence(
            storage, dimension, MAX_CITATIONS
        )
        first_party = sum(1 for hit in evidence if hit.voice is EvidenceVoice.FIRST_PARTY)
        third_party = len(evidence) - first_party
        dimensions.append(
            DimensionGap(
                dimension_id=dimension.id,
                name=dimension.name,
                asks=dimension.asks,
                coverage=_coverage(first_party, third_party),
                probe=dimension.probe,
                confusable_with=dimension.confusable_with,
                first_party=first_party,
                third_party=third_party,
                # Counts above are computed on the full match set; only the
                # listed citations are capped.
                evidence=evidence[:MAX_CITATIONS],
            )
        )

    _logger.info(
        "gap_map_built rubric=%s items_read=%d absent=%d",
        rubric.id,
        len(items),
        sum(1 for row in dimensions if row.coverage is Coverage.ABSENT),
    )
    return GapMap(
        rubric_id=rubric.id,
        rubric_title=rubric.title,
        rubric_version=rubric.version,
        provenance=rubric.provenance,
        dimensions=dimensions,
        items_read=len(items),
    )


_COVERAGE_HEADINGS: tuple[tuple[Coverage, str], ...] = (
    (Coverage.ABSENT, "Nothing to read"),
    (Coverage.THIRD_PARTY_ONLY, "Only somebody else's word for it"),
    (Coverage.THIN, "Thin"),
    (Coverage.EVIDENCED, "Evidenced"),
)


def render_gap_map(gap_map: GapMap) -> str:
    """The gap map as Markdown, gaps first.

    Ordering is the argument: what is missing leads, because that is what
    the reader can act on, and the evidenced dimensions are the part they
    already knew. Same shape as the completeness report's "Things to do".
    """
    lines: list[str] = [
        f"# Gap map — {gap_map.rubric_title}",
        "",
        (
            f"Rubric `{gap_map.rubric_id}` v{gap_map.rubric_version} "
            f"({gap_map.provenance.tier.value}) · read {gap_map.items_read} profile items · "
            f"rule `{gap_map.contract_version}`"
        ),
        "",
        f"> {gap_map.provenance.disclaimer}",
        "",
        (
            "This report says what evidence exists for each dimension. It does **not** "
            "place you on a rung, and no model was called to produce it."
        ),
        "",
    ]

    for coverage, heading in _COVERAGE_HEADINGS:
        rows = [row for row in gap_map.dimensions if row.coverage is coverage]
        if not rows:
            continue
        lines.append(f"## {heading}")
        lines.append("")
        for row in rows:
            counts = f"{row.first_party} first-party, {row.third_party} third-party"
            lines.append(f"### {row.name} ({counts})")
            lines.append("")
            lines.append(f"*{row.asks}*")
            lines.append("")
            if row.coverage is Coverage.ABSENT and row.probe:
                lines.append(f"- **Nothing matched.** To close it, ask: “{row.probe}”")
            if row.coverage is Coverage.THIRD_PARTY_ONLY:
                lines.append(
                    "- **Every match is somebody else vouching.** That is real, and it is "
                    "not the same claim as an artifact you can point at."
                )
                if row.probe:
                    lines.append(f"- To close it, ask: “{row.probe}”")
            if row.confusable_with:
                lines.append(f"- **Careful:** {row.confusable_with}")
            for hit in row.evidence:
                voice = "you" if hit.voice is EvidenceVoice.FIRST_PARTY else "someone else"
                signals = ", ".join(hit.matched[:3])
                lines.append(f"- [{hit.kind} · {voice}] {hit.excerpt} — matched: {signals}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
