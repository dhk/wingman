"""Google Meet / Gemini call transcripts, parsed (#134).

A real source of career substance has had no path in: actual conversations.
Gemini's notetaker produces a two-tab document — a Notes tab (summary, next
steps, a bulleted "Details" walkthrough) and a Transcript tab (timestamped
turns, one speaker per turn) — reachable as a Google Doc URL or exported to
PDF. This module turns either into a structure the rest of wingman can
reason about, and it does so deterministically: the export's own Markdown
headings carry everything, and AGENTS.md forbids a model for parsing that
has a correct answer.

Three properties of the real format drive the whole design.

**Only the transcript turns are anybody's words.** The Summary, Next steps
and Details sections are Gemini's paraphrase of the call, and Gemini says so
itself at the foot of the page ("You should review Gemini's notes to make
sure they're accurate"). Mining those for profile claims would file a
model's summary of what somebody said as the evidence that they said it —
the exact substitution RFC-026 exists to prevent. So `quotable_turns` reads
the Transcript tab only, and the Notes tab is carried as context.

**Speaker labels are a guess, and they are demonstrably wrong.** In the
first real export used to build this, the invitee is `Chuck Patel
<cpatel@champsinc.com>` and every one of his turns — plus the summary, plus
the next-steps item — attributes him as "Chuck Norris". Gemini invented a
surname. Attribution therefore comes from the `Invited` line, which carries
real names and real email addresses, and any speaker label that does not
match an invitee is REPORTED rather than reconciled. Storing a quote against
the wrong person is worse than storing nothing.

**A call is often both kinds at once.** #134 asks whether a transcript is
interview-style (mine it for profile evidence) or recruiter/company-style
(file it as a note), and the first real example is both: a vendor
conversation that also contains first-person claims worth keeping. So this
module classifies nothing. It parses, resolves participants, flags the
mismatches, and hands a preview to a human — the same plan-then-apply shape
`form_ingest` uses for the same reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from wingman.application.ingest import IngestError
from wingman.infrastructure.logs import get_logger

_logger = get_logger("application.transcript")

#: 'Invited [Name](mailto:addr) [Name](mailto:addr)' — the authoritative
#: participant list, and the only place a real email address appears.
_INVITED_LINE = re.compile(r"^\s*Invited\s+(.*)$", re.MULTILINE)
_MAILTO = re.compile(r"\[([^\]]+)\]\(mailto:([^)]+)\)")

#: '### 00:12:06' — a transcript section head.
_TIMESTAMP_HEAD = re.compile(r"^#{2,4}\s*\**\s*(\d{1,2}:\d{2}:\d{2})\s*\**\s*$", re.MULTILINE)

#: '**Speaker Name:** what they said', or the same line with the bold
#: already flattened away. Both shapes occur: a Markdown export keeps the
#: asterisks, and a PDF export — or anything that has been through
#: `extract_resume_text` — does not. Depending on the bold would make this
#: work on the file you tested and fail on the file somebody brings.
_SPEAKER_LINE = re.compile(r"^[ \t]*\*{0,2}([A-Z][^*:\n]{0,79}?)\*{0,2}\s*:\*{0,2}[ \t]*(.*)$")

_TRANSCRIPT_TAB = re.compile(r"^#\s.{0,8}?Transcript", re.MULTILINE)

#: A list bullet or a heading: marker then whitespace, or a '#' heading.
#: '**Speaker:**' must NOT match, which is why this keys on the space.
_BULLET_LINE = re.compile(r"^(?:[-+>]\s|\*\s|#{1,6}\s)")

#: Gemini's own footer disclaimer — kept as the reason the Notes tab is
#: never quoted as evidence.
_GEMINI_DISCLAIMER = "review Gemini's notes"


@dataclass(frozen=True)
class Participant:
    """Somebody the calendar invite named — name AND address, verbatim."""

    name: str
    email: str

    @property
    def domain(self) -> str:
        _, _, domain = self.email.partition("@")
        return domain.lower()


@dataclass(frozen=True)
class Turn:
    """One speaker's words, at one timestamp, exactly as transcribed."""

    timestamp: str
    speaker: str
    text: str


@dataclass
class Transcript:
    """A parsed Gemini export. Nothing here is interpreted, only located."""

    title: str = ""
    date: str = ""
    participants: list[Participant] = field(default_factory=list)
    #: Gemini's own summary — context, never evidence.
    summary: str = ""
    next_steps: str = ""
    turns: list[Turn] = field(default_factory=list)
    #: True when the export carries Gemini's "review these notes" footer.
    model_generated_notes: bool = False

    @property
    def speakers(self) -> list[str]:
        seen: dict[str, None] = {}
        for turn in self.turns:
            seen.setdefault(turn.speaker, None)
        return list(seen)

    def turns_by(self, speaker: str) -> list[Turn]:
        target = _fold(speaker)
        return [turn for turn in self.turns if _fold(turn.speaker) == target]


def _fold(name: str) -> str:
    return " ".join(name.split()).casefold()


def _first_name(name: str) -> str:
    parts = _fold(name).split()
    return parts[0] if parts else ""


def parse_transcript(text: str) -> Transcript:
    """Parse a Gemini export. Deterministic — the headings carry it all.

    Tolerant about what it cannot find: a PDF export, a doc with the Notes
    tab deleted, or a transcript somebody pasted in without the header all
    parse to whatever IS there. An export with no turns at all is the one
    refusal, because a transcript with nothing anybody said is not a
    transcript.
    """
    if not text.strip():
        raise IngestError("the transcript file is empty. Nothing was read.")

    transcript = Transcript(model_generated_notes=_GEMINI_DISCLAIMER in text)

    invited = _INVITED_LINE.search(text)
    if invited:
        transcript.participants = [
            Participant(name=" ".join(name.split()), email=email.strip())
            for name, email in _MAILTO.findall(invited.group(1))
        ]

    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip().strip("*").strip()
        if stripped and not transcript.title and " - " in stripped and ":" not in stripped:
            transcript.title = stripped.removesuffix(" - Transcript").strip()
            break

    date_match = re.search(r"^\s*([A-Z][a-z]{2} \d{1,2}, \d{4})\s*$", text, re.MULTILINE)
    if date_match:
        transcript.date = date_match.group(1)

    transcript.summary = _section_body(text, "Summary")
    transcript.next_steps = _section_body(text, "Next steps")
    transcript.turns = _parse_turns(text)

    if not transcript.turns:
        raise IngestError(
            "no speaker turns found — a Gemini export carries them as "
            "'**Speaker:** ...' under '### 00:00:00' headings. Nothing was read."
        )
    _logger.info(
        "transcript parsed turns=%d speakers=%d participants=%d",
        len(transcript.turns),
        len(transcript.speakers),
        len(transcript.participants),
    )
    return transcript


def _section_body(text: str, heading: str) -> str:
    """The prose under one Notes-tab heading, up to the next heading."""
    pattern = re.compile(
        rf"^#{{2,4}}\s*\**\s*{re.escape(heading)}\s*\**\s*$(.*?)(?=^#{{1,4}}\s|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _parse_turns(text: str) -> list[Turn]:
    """Turns from the Transcript tab only.

    The Notes tab also contains bold labels, so starting at the Transcript
    heading is what keeps a summary bullet out of the quotable set.
    """
    tab = _TRANSCRIPT_TAB.search(text)
    body = text[tab.start() :] if tab else text

    turns: list[Turn] = []
    marks = list(_TIMESTAMP_HEAD.finditer(body))
    if not marks:
        return _turns_in(body, timestamp="")
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        turns.extend(_turns_in(body[mark.end() : end], timestamp=mark.group(1)))
    return turns


def _turns_in(chunk: str, timestamp: str) -> list[Turn]:
    """Line-based rather than one big regex.

    A turn runs from its speaker line until the next one, so continuation
    lines belong to whoever was speaking. Scanning lines makes that
    obvious; a single pattern spanning turns has to encode the same rule as
    a lookahead and is far easier to get subtly wrong.
    """
    turns: list[Turn] = []
    speaker = ""
    said: list[str] = []

    def flush() -> None:
        if speaker and (text := " ".join(" ".join(said).split())):
            turns.append(Turn(timestamp=timestamp, speaker=speaker, text=text))

    for line in chunk.splitlines():
        stripped = line.strip()
        # A Markdown bullet is never a speaker turn: the Notes tab's
        # 'Details' section is full of '- **Topic**: prose', which would
        # otherwise parse as somebody called 'Topic' saying something.
        # Keyed on bullet SYNTAX (a marker then a space), not on a leading
        # asterisk — '**Speaker:**' starts with one too.
        if _BULLET_LINE.match(stripped):
            said.append(stripped)
            continue
        match = _SPEAKER_LINE.match(line)
        if match and not match.group(1).strip().endswith(("http", "https")):
            flush()
            speaker = " ".join(match.group(1).split())
            said = [match.group(2)]
            continue
        said.append(stripped)
    flush()
    return turns


# --------------------------------------------------------------------------
# Attribution
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SpeakerMatch:
    """One transcript speaker, against the invite list."""

    speaker: str
    participant: Participant | None
    #: Why it matched, or why it did not — printed with the preview.
    reason: str

    @property
    def confident(self) -> bool:
        return self.participant is not None


def match_speakers(transcript: Transcript) -> list[SpeakerMatch]:
    """Reconcile transcript speaker labels against the calendar invitees.

    The invite list wins, always. Gemini's speaker labels are a guess and
    the first real export proves how wrong one can be: 'Chuck Patel
    <cpatel@champsinc.com>' is transcribed throughout as 'Chuck Norris'.

    A full-name match is confident. A first-name match with exactly ONE
    invitee is reported as probable and still shown for confirmation,
    because that is precisely the case the Norris example produces and it
    must not become an automatic attribution. Anything else is unmatched.
    """
    matches: list[SpeakerMatch] = []
    for speaker in transcript.speakers:
        exact = [p for p in transcript.participants if _fold(p.name) == _fold(speaker)]
        if exact:
            matches.append(
                SpeakerMatch(speaker, exact[0], f"exact match for invitee {exact[0].email}")
            )
            continue
        first = [p for p in transcript.participants if _first_name(p.name) == _first_name(speaker)]
        if len(first) == 1:
            matches.append(
                SpeakerMatch(
                    speaker,
                    None,
                    f"first name only — the invite says {first[0].name!r} "
                    f"({first[0].email}), the transcript says {speaker!r}. Gemini's speaker "
                    "labels are generated and do get surnames wrong; confirm who this is",
                )
            )
            continue
        matches.append(
            SpeakerMatch(speaker, None, "no invitee with this name — nothing to attribute it to")
        )
    return matches


def quotable_turns(transcript: Transcript, speaker: str) -> list[Turn]:
    """The turns that may be quoted as one person's own words.

    Only the Transcript tab, and only the requested speaker. The Notes tab
    is a model's paraphrase — quoting it would file Gemini's sentence as
    evidence that a human said it.
    """
    return transcript.turns_by(speaker)


def render_transcript(transcript: Transcript, matches: list[SpeakerMatch]) -> str:
    """The preview. Nothing is stored on the strength of it."""
    lines = [
        f"Call: {transcript.title or '(untitled)'}",
        f"Date: {transcript.date or '(none in the export)'}",
        "",
        "Invited (from the calendar header — the authoritative names):",
    ]
    if transcript.participants:
        for participant in transcript.participants:
            lines.append(f"   {participant.name} <{participant.email}>")
    else:
        lines.append("   (none — this export has no Invited line)")
    lines.append("")

    lines.append(f"Speakers in the transcript ({len(transcript.turns)} turns):")
    for match in matches:
        marker = "ok" if match.confident else "CONFIRM"
        turns = len(transcript.turns_by(match.speaker))
        lines.append(f"   [{marker}] {match.speaker} — {turns} turn(s)")
        lines.append(f"          {match.reason}")
    lines.append("")

    if any(not match.confident for match in matches):
        lines.append(
            "Nothing is attributed to a speaker above that is not confirmed. A quote "
            "stored against the wrong person is worse than a quote not stored."
        )
        lines.append("")

    if transcript.summary:
        lines.append(
            "Gemini's own summary is carried as context and is NEVER quoted as evidence"
            + (
                " (the export carries Google's own 'review these notes' warning)"
                if transcript.model_generated_notes
                else ""
            )
            + ":"
        )
        for line in transcript.summary.splitlines()[:6]:
            if line.strip():
                lines.append(f"   {line.strip()}")
        lines.append("")

    if transcript.next_steps:
        lines.append("Next steps, as the export recorded them:")
        for line in transcript.next_steps.splitlines():
            if line.strip():
                lines.append(f"   {line.strip()}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
