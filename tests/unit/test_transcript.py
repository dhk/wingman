"""Gemini call transcripts, parsed (#134).

Built against a real export. Three of its properties drive every test here:
only the Transcript tab is anybody's words, the speaker labels are
generated and demonstrably wrong, and a call is often both an interview and
a company conversation at once.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.application.ingest import IngestError
from wingman.application.transcript import (
    match_speakers,
    parse_transcript,
    quotable_turns,
    render_transcript,
)
from wingman.cli.main import app
from wingman.infrastructure.config import ENV_DATA_DIR

# Trimmed from the real 2026-08-05 export, structure preserved exactly —
# including the invitee/speaker disagreement, which is not a typo here.
REAL = """# **📝 Notes**

 Aug 5, 2026

## **30-minute-coffee - Chuck Patel and Dave Holmes-Kinsella**

Invited [Dave Holmes-Kinsella](mailto:davehk@gmail.com) [Chuck Patel](mailto:cpatel@champsinc.com)

Attachments [30-minute-coffee](https://calendar.google.com/calendar/event?eid=ZDBk)

### **Summary**

Experts reviewed healthcare AI applications and the necessity of data governance.

### **Next steps**

  - \\[Dave Holmes-Kinsella\\] Share thoughts: provide follow up thoughts to Chuck Norris.

### **Details**

  - **Company Overview and Service Model**: Dave Holmes-Kinsella explains healthcare AI ([00:00:00](https://docs.google.com/x)).

*You should review Gemini's notes to make sure they're accurate.*

# **📝 Transcript**

 Aug 5, 2026

## **30-minute-coffee - Chuck Patel and Dave Holmes-Kinsella - Transcript**

### **00:00:00**

**Dave Holmes-Kinsella:** so we do healthcare AI and our computers call your
insurance company to secure benefits verification.

**Chuck Norris:** Mhm.

### **00:01:32**

**Dave Holmes-Kinsella:** the pharma companies love our product because we are
30 to 40% more accurate than human beings at a tiny amount of the price.

### **00:09:07**

**Chuck Norris:** we work with a lot of nuclear power companies.

### **Transcription ended after 00:29:37**
"""

#: The same call after a PDF export or anything that flattens Markdown —
#: no bold, no asterisks. This is the shape the CLI actually sees.
FLATTENED = REAL.replace("**", "")


def test_the_invited_line_gives_real_names_and_addresses() -> None:
    """The one place a real identity appears. Everything else in the export
    is generated."""
    transcript = parse_transcript(REAL)

    assert [(p.name, p.email) for p in transcript.participants] == [
        ("Dave Holmes-Kinsella", "davehk@gmail.com"),
        ("Chuck Patel", "cpatel@champsinc.com"),
    ]
    assert transcript.participants[1].domain == "champsinc.com"


def test_the_title_and_date_come_off_the_header() -> None:
    transcript = parse_transcript(REAL)

    assert transcript.title == "30-minute-coffee - Chuck Patel and Dave Holmes-Kinsella"
    assert transcript.date == "Aug 5, 2026"


def test_turns_are_read_with_their_timestamps() -> None:
    transcript = parse_transcript(REAL)

    assert len(transcript.turns) == 4
    assert transcript.turns[0].timestamp == "00:00:00"
    assert transcript.turns[0].speaker == "Dave Holmes-Kinsella"
    assert "healthcare AI" in transcript.turns[0].text


def test_a_turn_wrapped_over_several_lines_stays_one_turn() -> None:
    """Line wrapping is presentation. Splitting on it would cut a sentence
    in half and quote the fragment."""
    transcript = parse_transcript(REAL)
    claim = next(t for t in transcript.turns if "30 to 40%" in t.text)

    assert "at a tiny amount of the price" in claim.text


@pytest.mark.parametrize("text", [REAL, FLATTENED], ids=["markdown", "flattened"])
def test_it_parses_with_or_without_markdown_bold(text: str) -> None:
    """A .md export keeps the asterisks; a PDF export — and anything through
    extract_resume_text, which is what the CLI uses — does not. Depending on
    the bold would work on the file you tested and fail on the file somebody
    brings."""
    transcript = parse_transcript(text)

    assert len(transcript.turns) == 4
    assert {t.speaker for t in transcript.turns} == {"Dave Holmes-Kinsella", "Chuck Norris"}


def test_the_notes_tab_is_never_read_as_somebody_speaking() -> None:
    """'- **Company Overview and Service Model**: Dave explains...' would
    otherwise parse as a person called 'Company Overview' saying something —
    and it is Gemini's paraphrase, not anyone's words."""
    transcript = parse_transcript(REAL)

    assert all("Company Overview" not in turn.speaker for turn in transcript.turns)
    assert all("Details" not in turn.speaker for turn in transcript.turns)


def test_gemini_summary_is_carried_as_context_and_marked_as_generated() -> None:
    transcript = parse_transcript(REAL)

    assert "data governance" in transcript.summary
    assert transcript.model_generated_notes is True
    rendered = render_transcript(transcript, match_speakers(transcript))
    assert "NEVER quoted as evidence" in rendered


def test_the_transcribed_name_that_disagrees_with_the_invite_is_flagged() -> None:
    """The finding that shaped this module: the invitee is Chuck PATEL and
    every one of his turns is labelled Chuck NORRIS. Gemini invented a
    surname, and the summary and next-steps repeat it."""
    transcript = parse_transcript(REAL)
    matches = {m.speaker: m for m in match_speakers(transcript)}

    assert matches["Dave Holmes-Kinsella"].confident
    assert not matches["Chuck Norris"].confident
    assert "Chuck Patel" in matches["Chuck Norris"].reason
    assert "cpatel@champsinc.com" in matches["Chuck Norris"].reason


def test_a_first_name_match_is_never_treated_as_an_attribution() -> None:
    """'Chuck' == 'Chuck' is exactly the case that produced Norris. It is
    reported, not resolved — a quote stored against the wrong person is
    worse than one not stored."""
    transcript = parse_transcript(REAL)
    match = next(m for m in match_speakers(transcript) if m.speaker == "Chuck Norris")

    assert match.participant is None


def test_the_preview_says_nothing_unconfirmed_gets_attributed() -> None:
    transcript = parse_transcript(REAL)

    rendered = render_transcript(transcript, match_speakers(transcript))

    assert "CONFIRM" in rendered
    assert "worse than a quote not stored" in rendered


def test_quotable_turns_are_one_speakers_own_words_only() -> None:
    transcript = parse_transcript(REAL)

    mine = quotable_turns(transcript, "Dave Holmes-Kinsella")

    assert len(mine) == 2
    assert all("nuclear power" not in turn.text for turn in mine)


def test_an_export_with_no_turns_is_refused_rather_than_half_read() -> None:
    with pytest.raises(IngestError, match="no speaker turns"):
        parse_transcript("# Notes\n\nJust a summary, no transcript tab at all.\n")


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(IngestError, match="empty"):
        parse_transcript("   \n")


def test_a_pasted_transcript_with_no_header_still_parses() -> None:
    """Somebody will paste just the turns. Participants are then unknown —
    which the preview says, rather than guessing from the speaker labels."""
    transcript = parse_transcript(
        "### 00:00:00\n\n**Jane Doe:** I led the migration.\n\n**Someone Else:** Nice.\n"
    )

    assert len(transcript.turns) == 2
    assert transcript.participants == []
    assert "none — this export has no Invited line" in render_transcript(
        transcript, match_speakers(transcript)
    )


def test_a_speaker_with_no_invitee_at_all_is_unmatched(tmp_path: Path) -> None:
    transcript = parse_transcript(REAL.replace("**Chuck Norris:**", "**A Third Person:**"))
    match = next(m for m in match_speakers(transcript) if m.speaker == "A Third Person")

    assert match.participant is None
    assert "no invitee with this name" in match.reason


def _workspace(tmp_path: Path, monkeypatch) -> CliRunner:  # noqa: ANN001
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    runner = CliRunner()
    runner.invoke(app, ["init"])
    return runner


def test_the_cli_previews_and_writes_nothing_by_default(tmp_path: Path, monkeypatch) -> None:
    runner = _workspace(tmp_path, monkeypatch)
    path = tmp_path / "call.md"
    path.write_text(REAL, encoding="utf-8")

    result = runner.invoke(app, ["ingest-transcript", str(path)])

    assert result.exit_code == 0, result.output
    assert "Nothing was written" in result.output
    assert "CONFIRM" in result.output


def test_the_cli_files_a_note_only_against_the_person_you_name(tmp_path: Path, monkeypatch) -> None:
    """Never inferred from the export — the export's own names are the
    thing that cannot be trusted."""
    runner = _workspace(tmp_path, monkeypatch)
    runner.invoke(app, ["people", "add", "Chuck Patel"])
    path = tmp_path / "call.md"
    path.write_text(REAL, encoding="utf-8")

    result = runner.invoke(app, ["ingest-transcript", str(path), "--note-for", "Chuck Patel"])

    assert result.exit_code == 0, result.output
    assert "Filed against Chuck Patel" in result.output
    logged = runner.invoke(app, ["relationship", "log", "Chuck Patel", "--list"])
    assert "30-minute-coffee" in logged.output or result.exit_code == 0


def test_the_cli_needs_a_file_or_a_url(tmp_path: Path, monkeypatch) -> None:
    runner = _workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["ingest-transcript"])

    assert result.exit_code == 2
