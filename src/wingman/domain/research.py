"""Approved research sources and their fetched snapshots (RFC-015).

A CompanySource is a page the user explicitly trusts for one company —
adding it is the approval. A ResearchSnapshot is what one explicit fetch
of that page saw: a hash of its visible text and the set of links it
carried. Research findings are diffs between snapshots (new links, changed
text), never model claims.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class CompanySource(BaseModel):
    """One user-approved research URL for a company.

    retain opts this one page into keeping its prose as a document (RFC-060):
    off by default, because a careers page re-stored on every run is churn,
    while an about/values page changes twice a year and is the only thing a
    stance can quote. The fetch itself is identical either way.
    """

    company_key: str
    company_name: str
    url: str
    label: str | None = None
    paused: bool = False
    retain: bool = False
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ResearchSnapshot(BaseModel):
    """What one fetch of an approved source saw. Replaced on refresh; the
    diff against the previous snapshot is reported at replace time."""

    company_key: str
    url: str
    text_hash: str
    links: list[str] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class NewLinkEvent(BaseModel):
    """One link first seen as 'new' during a research fetch. Snapshots are
    replace-on-refresh and only ever hold the latest link set, so this is the
    accumulating record a dossier draws its 'new since last dossier' section
    from — first sighting wins, it is never overwritten."""

    company_key: str
    url: str
    source_url: str
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
