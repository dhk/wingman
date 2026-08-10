"""Whitespace-tolerant evidence matching (RFC-026).

The trigger: a hand-maintained source-of-truth resume whose bullets are
hard-wrapped mid-sentence. The model quotes the sentence it reads; the
byte-for-byte verbatim check couldn't find it across the line break, and
the import rejected the strongest achievements. Every verbatim gate now
folds whitespace on both sides — content still matches character for
character, only wrapping and indentation are forgiven.
"""

import json
from pathlib import Path

import pytest

from wingman.application.evidence import fold_whitespace, locate_quote
from wingman.application.ingest import ingest_resume
from wingman.application.pov import _validate_proposal
from wingman.domain.pov import PovProposal, ProposedStance
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.recorded import RecordedProvider

WRAPPED_SOURCE = """\
# Dave — Wingman Source of Truth

### Infinitus Systems — AI Healthcare Automation
**Head of Data (Leader & Hands-On IC) • 2024–Present**
- Redesigned outreach scheduling with probabilistic outcome modeling; completion
  rate 0.89% → 4.95% across 100K+ member interactions.

### Synctera — Banking-as-a-Service Platform
- Architected financial data integrity platform supporting $100B+ in ledger
  activity; to-the-penny reconciliation across banking partners.
"""

# The quotes a model actually produces: the sentence as read, unwrapped.
RESPONSE = json.dumps(
    {
        "items": [
            {
                "kind": "achievement",
                "name": "Outreach scheduling redesign",
                "detail": "Probabilistic outcome modeling at Infinitus.",
                "classification": "fact",
                "confidence": 0.9,
                "quotes": [
                    (
                        "Redesigned outreach scheduling with probabilistic outcome "
                        "modeling; completion rate 0.89% → 4.95% across 100K+ "
                        "member interactions."
                    )
                ],
            },
            {
                "kind": "achievement",
                "name": "Financial data integrity platform",
                "detail": "Ledger reconciliation at Synctera.",
                "classification": "fact",
                "confidence": 0.9,
                "quotes": [
                    (
                        "Architected financial data integrity platform supporting "
                        "$100B+ in ledger activity; to-the-penny reconciliation "
                        "across banking partners."
                    )
                ],
            },
            {
                "kind": "achievement",
                "name": "Fabricated claim",
                "detail": "Should still be rejected.",
                "classification": "fact",
                "confidence": 0.9,
                "quotes": ["Single-handedly rebuilt the entire company."],
            },
        ]
    }
)


def _workspace(tmp_path: Path) -> Config:
    config = load_config(env={ENV_DATA_DIR: str(tmp_path / "ws")})
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def test_fold_whitespace() -> None:
    assert fold_whitespace("a  b\n   c\td") == "a b c d"
    assert fold_whitespace("  edges  ") == "edges"
    assert fold_whitespace("") == ""
    # content differences are never forgiven
    assert fold_whitespace("0.89%") != fold_whitespace("0.89 %")


@pytest.mark.parametrize("suffix", [".md", ".txt"])
def test_hard_wrapped_source_of_truth_ingests(tmp_path: Path, suffix: str) -> None:
    """The regression: quotes spanning a hard wrap used to be rejected.

    .md exercises the Markdown normalizer; .txt (no normalization, the
    shape PDF extraction also produces) exercises the whitespace fold.
    """
    config = _workspace(tmp_path)
    resume = tmp_path / f"source_of_truth{suffix}"
    resume.write_text(WRAPPED_SOURCE, encoding="utf-8")
    with Storage(config.db_path) as storage:
        report = ingest_resume(resume, config, storage, RecordedProvider(RESPONSE))
    assert report.accepted == 2
    assert [item.name for item in report.rejected] == ["Fabricated claim"]
    assert "not found verbatim" in report.rejected[0].reason


def test_pov_quote_across_wrapped_corpus_body() -> None:
    proposal = PovProposal(
        stances=[
            ProposedStance(
                statement="Ships small and reversible changes.",
                quote="we ship small, reversible changes every single day",
                doc_id="d1",
            ),
            ProposedStance(
                statement="Invented position.",
                quote="text that appears nowhere",
                doc_id="d1",
            ),
        ]
    )
    docs = {"d1": ("Ship Small", "r1", None)}
    bodies = {"d1": "On process: we ship small,\n  reversible changes\nevery single day.\n"}
    stances, rejected = _validate_proposal(proposal, docs, bodies)
    assert [stance.statement for stance in stances] == ["Ships small and reversible changes."]
    assert [entry.reason for entry in rejected] == [
        "quote does not appear verbatim in 'Ship Small'"
    ]


def test_locate_quote_matches_across_missing_spaces_and_returns_the_source_span() -> None:
    """The #278 case: a PDF whose fonts carry no space glyphs extracts as
    'HeadofDataScience2021-2024'. The model reads words and quotes words.
    Folding whitespace runs cannot insert spaces that were never there."""
    source = "EXPERIENCE Synctera\nHeadofDataScience2021-2024\nDirector, Data"
    found = locate_quote("Head of Data Science 2021-2024", source)

    assert found == "HeadofDataScience2021-2024"
    # The citation quotes the document, not the model's readable rewrite.
    assert found in source


def test_locate_quote_still_bridges_a_hard_line_wrap() -> None:
    """RFC-026's original case must keep working."""
    source = "Built a reconciliation analytics workbench that delivered\n6x productivity."
    assert (
        locate_quote("workbench that delivered 6x productivity.", source)
        == "workbench that delivered 6x productivity."
    )


def test_locate_quote_rejects_characters_that_are_not_there() -> None:
    """Ignoring whitespace costs nothing in strength: every non-space
    character must still appear, in order. This is the invented-evidence
    case the whole check exists to catch."""
    source = "Improved completion from 0.89% to 4.95%."
    assert locate_quote("Improved completion from 0.89% to 9.95%.", source) is None
    assert locate_quote("Led a team of forty engineers.", source) is None
    # Right characters, wrong order.
    assert locate_quote("4.95% to 0.89%", source) is None


def test_locate_quote_rejects_blank_quotes() -> None:
    assert locate_quote("", "anything") is None
    assert locate_quote("   \n\t ", "anything") is None


def test_locate_quote_folds_the_padding_layout_extraction_leaves_behind() -> None:
    """pypdf's layout mode pads with columns of spaces to preserve position.
    A faithful span would cite 'Head  of Data   Science            2021-2024';
    folding keeps every source character, in order, and reads (#278)."""
    source = "Head  of Data   Science                    2021-2024"
    assert locate_quote("Head of Data Science 2021-2024", source) == (
        "Head of Data Science 2021-2024"
    )


def test_locate_quote_matches_across_typographic_punctuation() -> None:
    """A PDF says 'Uber’s' (U+2019); a model quotes 'Uber's' (U+0027),
    because that is how the text reads. One character sank an entire
    achievement before this (#280)."""
    source = "Improved Design Efficiency by >10x by inventing a precursor to Uber’s H3."
    found = locate_quote("inventing a precursor to Uber's H3.", source)

    assert found == "inventing a precursor to Uber’s H3."
    # The citation keeps the document's real typography, not the ASCII rewrite.
    assert "’" in found


def test_locate_quote_matches_across_dash_variants() -> None:
    source = "Head of Data Science 2021–2024"  # en dash
    assert locate_quote("Head of Data Science 2021-2024", source) == source
    # …and the other direction, since either side may be the typographic one.
    assert locate_quote("2021—2024", "worked 2021-2024 there") == "2021-2024"


def test_locate_quote_matches_across_curly_double_quotes() -> None:
    source = "wrote the “why-to” book on the tool"
    assert locate_quote('wrote the "why-to" book', source) == "wrote the “why-to” book"


def test_punctuation_folding_does_not_forgive_content() -> None:
    """Only presentation is forgiven. Letters and digits still must match."""
    source = "Uber’s H3, improved 10x"
    assert locate_quote("Lyft's H3", source) is None
    assert locate_quote("Uber's H4", source) is None
    assert locate_quote("Uber's H3, improved 20x", source) is None
