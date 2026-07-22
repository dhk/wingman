"""#97: one JSON extractor for every agent, tolerant of fences AND commentary."""

import pytest

from wingman.agents._json import extract_json_block
from wingman.agents.opportunity_analyst import parse_requirements
from wingman.agents.profile_curator import ProposalParseError

THE_97_SHAPE = '```\n{"requirements": []}\n```\n\nThe provided text is a job listing page index showing multiple open positions...'


def test_fenced_json_with_trailing_commentary_parses() -> None:
    assert extract_json_block(THE_97_SHAPE) == {"requirements": []}


def test_fence_variants() -> None:
    assert extract_json_block('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_block("```JSON\n[1, 2]\n```") == [1, 2]
    assert extract_json_block('prose first\n```\n{"a": 1}\n```\nprose after') == {"a": 1}


def test_bare_json_and_surrounding_prose() -> None:
    assert extract_json_block('{"a": 1}') == {"a": 1}
    assert extract_json_block('Here you go:\n{"a": 1}\nHope that helps!') == {"a": 1}
    assert extract_json_block('{"a": 1} trailing words') == {"a": 1}


def test_no_json_is_a_clean_error() -> None:
    with pytest.raises(ValueError, match="contains no JSON"):
        extract_json_block("the vibes are good")
    with pytest.raises(ValueError, match="not valid JSON"):
        extract_json_block('{"a": unterminated')


def test_parse_requirements_survives_trailing_commentary() -> None:
    """The exact crash from #97, end to end through the agent parser."""
    proposal = parse_requirements(THE_97_SHAPE)
    assert proposal.requirements == []
    with pytest.raises(ProposalParseError, match="no JSON"):
        parse_requirements("nothing extractable here")
