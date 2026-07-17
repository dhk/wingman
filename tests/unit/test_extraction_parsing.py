import pytest

from wingman.agents.profile_curator import (
    PROMPT_VERSION,
    ProposalParseError,
    build_prompt,
    parse_proposal,
)

VALID = (
    '{"items": [{"kind": "skill", "name": "Python", "classification": "fact",'
    ' "confidence": 0.9, "quotes": ["Python"]}]}'
)


def test_parses_valid_output() -> None:
    proposal = parse_proposal(VALID)
    assert proposal.items[0].name == "Python"
    assert proposal.items[0].quotes == ["Python"]


def test_strips_markdown_fences() -> None:
    proposal = parse_proposal(f"```json\n{VALID}\n```")
    assert len(proposal.items) == 1


def test_rejects_invalid_json() -> None:
    with pytest.raises(ProposalParseError, match="not valid JSON"):
        parse_proposal("here are the items you asked for")


def test_rejects_schema_violations() -> None:
    with pytest.raises(ProposalParseError, match="schema"):
        parse_proposal('{"items": [{"kind": "skill", "name": "Python"}]}')


def test_rejects_items_without_quotes() -> None:
    with pytest.raises(ProposalParseError, match="schema"):
        parse_proposal(
            '{"items": [{"kind": "skill", "name": "Python", "classification": "fact",'
            ' "confidence": 0.9, "quotes": []}]}'
        )


def test_prompt_embeds_resume_text() -> None:
    prompt = build_prompt("UNIQUE-RESUME-MARKER")
    assert "UNIQUE-RESUME-MARKER" in prompt
    assert "verbatim" in prompt
    assert PROMPT_VERSION == "profile_extraction_v1"
