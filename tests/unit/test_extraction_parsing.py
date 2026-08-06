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
    with pytest.raises(ProposalParseError, match="no JSON"):
        parse_proposal("here are the items you asked for")
    with pytest.raises(ProposalParseError, match="not valid JSON"):
        parse_proposal('{"items": [unterminated')


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
    # Pinned deliberately: PROMPT_VERSION is stamped on every extracted item,
    # so a bump is a real event that should have to be written down here too.
    assert PROMPT_VERSION == "profile_extraction_v2"


def test_prompt_asks_for_roles_and_their_structure() -> None:
    """v1 said 'Extract achievements and skills' and offered only those two
    in its schema, which is the whole reason Roles had no producer (#269).
    Asserting the version alone would not have caught that."""
    prompt = build_prompt("x")
    assert "roles" in prompt.lower()
    for field in ("company", "title", "started", "ended"):
        assert field in prompt
    assert '"role"' in prompt
    # The instruction that stops a current role acquiring an invented end date.
    assert "current" in prompt.lower()
