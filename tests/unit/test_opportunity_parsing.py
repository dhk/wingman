import pytest

from wingman.agents.opportunity_analyst import (
    build_assessment_prompt,
    build_requirements_prompt,
    parse_assessments,
    parse_requirements,
)
from wingman.agents.profile_curator import ProposalParseError
from wingman.domain.opportunity import Requirement, RequirementKind
from wingman.domain.profile import EvidenceSpan


def test_parses_requirements() -> None:
    proposal = parse_requirements(
        '{"requirements": [{"name": "Python", "kind": "required", "quotes": ["Python"]}]}'
    )
    assert proposal.requirements[0].kind is RequirementKind.REQUIRED


def test_rejects_requirement_without_quotes() -> None:
    with pytest.raises(ProposalParseError, match="schema"):
        parse_requirements(
            '{"requirements": [{"name": "Python", "kind": "required", "quotes": []}]}'
        )


def test_parses_assessments() -> None:
    proposal = parse_assessments(
        '{"assessments": [{"requirement_id": "r1", "verdict": "gap", "confidence": 0.5}]}'
    )
    assert proposal.assessments[0].evidence_item_ids == []


def test_rejects_unknown_verdict() -> None:
    with pytest.raises(ProposalParseError, match="schema"):
        parse_assessments(
            '{"assessments": [{"requirement_id": "r1", "verdict": "perfect", "confidence": 0.5}]}'
        )


def test_prompts_embed_inputs() -> None:
    assert "JOB-MARKER" in build_requirements_prompt("JOB-MARKER")
    requirement = Requirement(
        name="Python",
        kind=RequirementKind.REQUIRED,
        evidence=[EvidenceSpan(source_record_id="s1", quote="Python")],
    )
    prompt = build_assessment_prompt([requirement], [])
    assert requirement.requirement_id in prompt
