"""A Google Form carrying the interview questions, per tenant (#287).

The interview is conversational, which suits some people and not others.
Someone who would rather answer everything once, offline, in their own
time has had no way to do that — and the result is an empty profile,
which is the same outcome as never being asked.

wingman does NOT create the form. It emits a self-contained Apps Script
the owner pastes into script.google.com and runs once; the owner's own
Google session is the authentication, so wingman never holds a Google
token. That is the deliberate middle option of #287: the Forms API would
mean OAuth against the owner's account and wingman's first credentialed
Google integration, and a document to retype by hand would cost the same
effort every time and produce nothing consistent to ingest against.

Google Forms cannot import questions from a spreadsheet — its own
"import questions" reads another Form — so a script is the only artefact
that actually builds a form without the API.

The questions are not invented here, and the first version of this file
broke that rule: it carried a block of screening questions ("Have you
shipped an AI/LLM product?", years of experience, field of study) lifted
from one person's own workspace. Those are that person's application
answers, not an interview — a form built from them asks everyone else
about a stranger. They are gone.

What remains is what already exists and has no offline path: RFC-035's
five job-criteria areas (job_scoring.INTERVIEW_AREAS), and the whole
nomination set from application/interview.py — people admired and its
opposite, the company fallback for anyone who would rather not name
people, and organizations with their purpose. Nothing here is specific
to any one person.

Every question carries its DESTINATION, because the kinds of answer go
to different places — the distinction #283 had to make for qa_capture.
The manifest emitted alongside the script is what a later ingest reads
to route each response; the form itself is just a form.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from wingman.application.job_scoring import INTERVIEW_AREAS

# Where an answer goes once it comes back. 'criteria' is the job-criteria
# document (RFC-035, drives opening scoring), 'profile' is a cited profile
# item, 'answers' is the application answer bank (RFC-030).
Destination = Literal["criteria", "profile", "answers"]
Widget = Literal["short", "paragraph", "scale"]


class FormQuestion(BaseModel):
    """One question, its widget, and where its answer belongs."""

    key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    help: str = ""
    widget: Widget = "paragraph"
    destination: Destination = "profile"
    # The interview subtype an answer should be captured under, for the
    # nomination questions (application/interview.py's VALID_SUBTYPES).
    subtype: str = ""
    required: bool = False


def _criteria_questions() -> list[FormQuestion]:
    """RFC-035's five areas, verbatim — the same prompts the conversational
    review uses, so the two paths ask the same thing."""
    return [
        FormQuestion(
            key=f"criteria_{name.lower().replace(' ', '_').replace('-', '_')}",
            title=f"Job criteria — {name}",
            help=prompt,
            widget="paragraph",
            destination="criteria",
        )
        for name, prompt in INTERVIEW_AREAS
    ]


# The interview proper, from application/interview.py's subtypes: people
# admired and its opposite, then organizations. The wording follows
# interview_react's own docstring — including values_con's exclusion of the
# too-easy answer, and the fallback pair offered when someone would rather
# name companies than people.
_NOMINATIONS = [
    FormQuestion(
        key="values_pro",
        title="Name three people, living or dead, you would have dinner with — and why.",
        help="One per line, with a sentence on why for each. Anyone at all: they "
        "do not have to be famous, in your field, or still alive.",
        destination="profile",
        subtype="values_pro",
    ),
    FormQuestion(
        key="values_con",
        title="Name three people you would be horrified to see your name printed "
        "alongside — and why.",
        help="One per line, with why. Not Hitler — too easy an answer to tell us "
        "anything about you.",
        destination="profile",
        subtype="values_con",
    ),
    FormQuestion(
        key="network_admired",
        title="Which people you actually know do you admire, and why?",
        help="People you have worked with or met, not public figures. Paste their "
        "LinkedIn URLs if you have them — one per line, with why for each.",
        destination="profile",
        subtype="network_admired",
    ),
    FormQuestion(
        key="values_fallback_pro",
        title="Whose products or services are you proud to buy — and why?",
        help="Companies, if naming people is hard. One per line, with why.",
        destination="profile",
        subtype="values_fallback_pro",
    ),
    FormQuestion(
        key="values_fallback_con",
        title="Whose products or services would you never buy — and why?",
        destination="profile",
        subtype="values_fallback_con",
    ),
    FormQuestion(
        key="mission_alignment_pro",
        title="Name an organization you would be proud to be associated with, and "
        "say what you understand its primary purpose to be.",
        help="A company, a club, any group of people aligned for a purpose. The "
        "purpose in your own words — 'Pepsi sells cola' is the right level.",
        destination="profile",
        subtype="mission_alignment_pro",
    ),
    FormQuestion(
        key="mission_alignment_con",
        title="Name an organization you would be horrified to be associated with, "
        "and what you understand its primary purpose to be.",
        destination="profile",
        subtype="mission_alignment_con",
    ),
]


def form_questions() -> list[FormQuestion]:
    """Every question the form carries, in the order it asks them.

    Criteria first: they are the shortest to answer and the most immediately
    useful, since an unseeded criteria document means every opening arrives
    unscored.
    """
    return [*_criteria_questions(), *_NOMINATIONS]


_SECTIONS: list[tuple[str, str, Destination]] = [
    (
        "What you are looking for",
        "These answers become the criteria every new opening is scored against.",
        "criteria",
    ),
    (
        "People and organizations",
        "Who you admire and who you would not want to be associated with — and "
        "the same for organizations. These are about what you value, not what "
        "you can do. There are no wrong answers and nothing here is scored.",
        "profile",
    ),
]


def _js(value: str) -> str:
    """A JS string literal. JSON strings are valid JS strings, and json.dumps
    escapes quotes, backslashes and newlines — which matter because these
    prompts contain apostrophes and em dashes."""
    return json.dumps(value, ensure_ascii=False)


def _item_call(question: FormQuestion) -> list[str]:
    builder = {
        "short": "addTextItem()",
        "paragraph": "addParagraphTextItem()",
        "scale": "addScaleItem()",
    }[question.widget]
    lines = [f"  item = form.{builder};", f"  item.setTitle({_js(question.title)});"]
    if question.help:
        lines.append(f"  item.setHelpText({_js(question.help)});")
    if question.required:
        lines.append("  item.setRequired(true);")
    return lines


def render_apps_script(label: str, questions: list[FormQuestion] | None = None) -> str:
    """The .gs file the owner pastes into script.google.com and runs once."""
    questions = form_questions() if questions is None else questions
    title = f"Wingman interview — {label}"
    lines = [
        # Every step here was learned by getting it wrong. "Paste this in, then
        # Run" is not enough: the editor ships a myFunction stub that stays in
        # the run dropdown, and a file that doesn't parse refuses to save — so
        # the first symptom is "Attempted to execute myFunction, but could not
        # save", which names neither cause.
        "/**",
        f" * Wingman interview form for: {label}",
        " *",
        " * 1. Open https://script.google.com and start a New project.",
        " * 2. Open the Code.gs file it gives you. SELECT ALL and paste over it —",
        " *    the myFunction stub must be gone, not left above this.",
        " * 3. Save (the disk icon). If it will not save, the paste is broken:",
        " *    this file must start with the /** on line 1 and nothing before it.",
        " * 4. In the dropdown at the top, choose createWingmanInterviewForm.",
        " *    It defaults to myFunction, which no longer exists.",
        " * 5. Run. Google asks for permission the first time — it is creating a",
        " *    form in YOUR Drive; wingman holds no Google credential of any kind.",
        " * 6. Open Execution log for the two URLs: send the published one to the",
        " *    person, keep the edit one. Running it twice creates a second form.",
        " */",
        "function createWingmanInterviewForm() {",
        f"  var form = FormApp.create({_js(title)});",
        f"  form.setDescription({_js(_DESCRIPTION.format(label=label))});",
        "  form.setCollectEmail(false);",
        "  var item;",
    ]
    for heading, blurb, destination in _SECTIONS:
        section = [q for q in questions if q.destination == destination]
        if not section:
            continue
        lines += [
            "",
            f"  // {heading}",
            "  item = form.addSectionHeaderItem();",
            f"  item.setTitle({_js(heading)});",
            f"  item.setHelpText({_js(blurb)});",
        ]
        for question in section:
            lines.append("")
            lines += _item_call(question)
    lines += [
        "",
        '  Logger.log("Send this to the person: " + form.getPublishedUrl());',
        '  Logger.log("Keep this for yourself:   " + form.getEditUrl());',
        "}",
        "",
    ]
    return "\n".join(lines)


_DESCRIPTION = (
    "Answer whatever you like, in whatever order — every question can be left "
    "blank, and a short honest answer beats a long careful one. Nothing here is "
    "graded. Your answers go into {label}'s own workspace and are used to find "
    "and score roles; they are stored in your own words, exactly as you write "
    "them."
)


def render_manifest(label: str, questions: list[FormQuestion] | None = None) -> str:
    """The routing table an ingest reads to put each answer where it belongs.

    Keyed by question TITLE, because that is the only thing a Forms response
    export carries back — the form has no idea what a destination is.
    """
    questions = form_questions() if questions is None else questions
    return json.dumps(
        {
            "tenant": label,
            "form_title": f"Wingman interview — {label}",
            "questions": [
                {
                    "key": question.key,
                    "title": question.title,
                    "destination": question.destination,
                    "subtype": question.subtype,
                }
                for question in questions
            ],
        },
        indent=2,
        ensure_ascii=False,
    )
