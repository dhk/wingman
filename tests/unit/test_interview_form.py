"""The per-tenant interview form generator (#287).

wingman emits an Apps Script; the owner's own Google session runs it. So
the thing worth testing is that the artefact is correct and safe to paste:
valid JavaScript, every question present, and the routing table honest
about where each answer goes.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from wingman.application.interview_form import (
    form_questions,
    render_apps_script,
    render_manifest,
)
from wingman.application.job_scoring import INTERVIEW_AREAS


def test_every_criteria_area_is_asked() -> None:
    """The five RFC-035 areas drive opening scoring. A form that skips one
    leaves that filter unseeded, and the conversational path would have
    asked it."""
    script = render_apps_script("jason")
    for name, prompt in INTERVIEW_AREAS:
        assert f"Job criteria — {name}" in script
        assert prompt.split(":")[0][:40] in script


def test_questions_are_grouped_by_where_their_answers_go() -> None:
    questions = form_questions()
    destinations = {question.destination for question in questions}
    assert destinations == {"criteria", "profile", "answers"}
    # Preferences are criteria, not skills — filed as profile items they score
    # nothing and masquerade as skills (#283).
    office = next(q for q in questions if q.key == "pref_office")
    assert office.destination == "criteria"
    # A screening question is an employer's question, not a career claim.
    shipped = next(q for q in questions if q.key == "screen_ai_product")
    assert shipped.destination == "answers"


def test_nomination_questions_carry_their_interview_subtype() -> None:
    """An answer has to be capturable under the right subtype, or the
    values/mission machinery cannot use it."""
    from wingman.application.interview import VALID_SUBTYPES

    nominations = [q for q in form_questions() if q.subtype]
    assert nominations
    for question in nominations:
        assert question.subtype in VALID_SUBTYPES


def test_question_titles_are_unique() -> None:
    """The manifest routes by TITLE, because a Forms response export carries
    nothing else back. Two questions sharing a title would be unroutable."""
    titles = [question.title for question in form_questions()]
    assert len(titles) == len(set(titles))


def test_manifest_routes_every_question_and_names_the_tenant() -> None:
    manifest = json.loads(render_manifest("jason"))
    assert manifest["tenant"] == "jason"
    assert manifest["form_title"] == "Wingman interview — jason"
    assert len(manifest["questions"]) == len(form_questions())
    for entry in manifest["questions"]:
        assert entry["destination"] in {"criteria", "profile", "answers"}
        assert entry["title"]


def test_the_tenant_travels_with_the_form() -> None:
    """'Jason's form, connected to Jason' — a generic questionnaire whose
    answers have to be matched up afterwards is the thing to avoid."""
    script = render_apps_script("jason")
    assert "Wingman interview — jason" in script
    assert "jason's own workspace" in script
    assert "jason" in json.loads(render_manifest("jason"))["tenant"]


def test_prompts_with_apostrophes_and_dashes_survive_as_js_literals() -> None:
    """These prompts are full of apostrophes and em dashes. Naive quoting
    would produce a script that fails to parse when pasted — after the owner
    has already emailed the link."""
    script = render_apps_script("jason")
    # 'Pepsi sells cola' is quoted inside a help string; an em dash is in the
    # description. Both must be escaped, not truncated.
    assert "Pepsi sells cola" in script
    assert "—" in script
    # No stray unescaped double quote can terminate a literal early.
    for line in script.splitlines():
        if line.strip().startswith(("form.", "item.", "var form")):
            assert line.rstrip().endswith((";", "{")), line


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_generated_script_is_valid_javascript() -> None:
    """The whole artefact is pasted into an editor and run once. A syntax
    error surfaces to the owner, mid-flow, with no way to tell whose fault
    it is."""
    script = render_apps_script("jason")
    result = subprocess.run(
        ["node", "--check", "-"], input=script, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_script_never_claims_wingman_holds_a_google_credential() -> None:
    """The whole point of this option over the Forms API (#287)."""
    script = render_apps_script("jason")
    assert "wingman holds no Google credential" in script
    assert "YOUR Drive" in script
