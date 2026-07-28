"""Interview captures read back as synthesis candidates (docs/PROFILE-BOOTSTRAP-DESIGN.md)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class InterviewDocument(BaseModel):
    """One captured interview reaction/nomination, assembled from a
    ProfileItem (kind=INTERVIEW) for build_own_pov to read alongside
    ExternalDocument/CorpusDocument.

    body is ALWAYS just the user's own 'why' — never the stimulus's or
    nominee's own content, which application/interview.py never persists
    in the first place. That is what keeps this synthesis-safe: the same
    verbatim-quote validation pov.py already applies to every document type
    can only ever validate a quote against the user's own words here, by
    construction, not by anything special this type has to defend.
    """

    doc_id: str
    title: str
    body: str
    published_at: datetime | None
    source_record_id: str
