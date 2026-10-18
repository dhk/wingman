"""Shared transcript preview and approved filing; original bytes stay attributable."""

from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from wingman.application.file_payload import archive_file
from wingman.application.ingest import IngestError
from wingman.application.relationship import log_interaction
from wingman.application.resume_formats import extract_resume_text
from wingman.application.transcript import (
    Transcript,
    match_speakers,
    parse_transcript,
    render_transcript,
)
from wingman.domain.relationship import EvidenceTier
from wingman.domain.source_record import SourceRecord
from wingman.infrastructure.config import Config
from wingman.infrastructure.storage import Storage


def transcript_note(transcript: Transcript) -> str:
    """Compose the exact, whitespace-normalized note that log_interaction stores."""
    summary = f"Call: {transcript.title or 'transcript'}"
    if transcript.date:
        summary += f" ({transcript.date})"
    summary += f". {len(transcript.turns)} turns transcribed by Gemini."
    if transcript.next_steps:
        summary += f" Next steps (Gemini-generated, user-endorsed): {transcript.next_steps}"
    return " ".join(summary.split())


def preview_transcript(raw: bytes, name: str) -> tuple[Transcript, str, str]:
    """Parse without persistent writes; bind the preview to the exact upload bytes."""
    with TemporaryDirectory(prefix="wingman-transcript-") as directory:
        path = Path(directory) / Path(name).name
        path.write_bytes(raw)
        try:
            transcript = parse_transcript(extract_resume_text(path))
        except IngestError:
            raise IngestError(
                "Transcript could not be read; check the export format. Nothing was filed."
            ) from None
    digest = hashlib.sha256(raw).hexdigest()
    preview = render_transcript(transcript, match_speakers(transcript))
    return (
        transcript,
        preview
        + f"\nExact note to file [endorsed]:\n{transcript_note(transcript)}\nReview hash: {digest}\n",
        digest,
    )


def file_transcript(
    raw: bytes,
    name: str,
    transcript: Transcript,
    person_name: str,
    reviewed_hash: str,
    config: Config,
    storage: Storage,
) -> str:
    """File only the reviewed bytes and exact recipient, as endorsed generated context.

    The hash binds the client's approval to these bytes; the client remains
    responsible for obtaining human approval. No receipt can prove that a
    person read a preview. No new identity or recipient is inferred here.
    """
    if reviewed_hash != hashlib.sha256(raw).hexdigest():
        raise IngestError(
            "Nothing was filed: preview first, then pass its reviewed_hash for the exact file."
        )
    person = storage.find_person_by_name_key(" ".join(person_name.lower().split()))
    if person is None:
        raise IngestError("Nothing was filed: name an existing person in full; see people_list.")
    previous = storage.find_transcript_filing(person.person_id, reviewed_hash)
    if previous is not None:
        return f"Already filed on {previous.happened_at.date().isoformat()} against {person.name}."
    summary = transcript_note(transcript)
    path = archive_file(Path(name).name, raw, config)
    locator = str(path.relative_to(config.data_dir))
    storage.add_source_record(
        SourceRecord(
            source_type="transcript",
            source_locator=locator,
            content_hash=hashlib.sha256(raw).hexdigest(),
            document_key=Path(name).name,
        )
    )
    log_interaction(
        person.name,
        summary,
        config,
        storage,
        evidence_tier=EvidenceTier.ENDORSED,
        source_url=locator,
    )
    return f"Filed against {person.name} [endorsed]: {summary}"
