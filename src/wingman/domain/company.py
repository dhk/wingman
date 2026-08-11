"""Company deep-dive records: sourced findings about an organisation (#350).

The person equivalent (`PersonDossier`, #222) is free text, because a
person's dossier is read by a human and nothing downstream consumes it.
A company dossier is different on both counts: it exists to be weighed
against a job-criteria document, and the follow-on the owner asked for is
comparison ACROSS companies. So the unit here is a `CompanyFinding` — one
claim, on one named dimension, with the source that supports it — not a
prose blob.

The source is a required, validated field rather than a convention: a
finding with no resolvable http(s) URL cannot be constructed, so no code
path (parser, importer, future caller) can store an unsourced claim by
forgetting to check. That is the same fail-closed shape RFC-058 chose for
the commentary store — structure, not vigilance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class CompanyDimension(StrEnum):
    """The three axes #350 names. A closed set, because the point is
    comparability: two companies can only be weighed against each other on
    dimensions they both carry."""

    MARKET_POSITION = "market_position"
    VALUES = "values"
    CULTURE = "culture"


DIMENSION_HEADINGS: dict[CompanyDimension, str] = {
    CompanyDimension.MARKET_POSITION: "Market position",
    CompanyDimension.VALUES: "Stated values",
    CompanyDimension.CULTURE: "Culture",
}


class CompanyFinding(BaseModel):
    """One cited claim about a company on one dimension.

    'quote' is the organisation's own words where the source carries them
    (stated values are worth far more verbatim than paraphrased) and empty
    otherwise — never invented to fill the field.
    """

    dimension: CompanyDimension
    claim: str = Field(min_length=1)
    quote: str = ""
    source_url: str = Field(min_length=1)
    source_title: str = ""

    @field_validator("source_url")
    @classmethod
    def _must_be_a_fetchable_url(cls, value: str) -> str:
        """Evidence has to be checkable by the reader. A bare domain, a
        paper title, or 'company website' is not a source someone can open,
        and this codebase does not store claims nobody can verify."""
        url = value.strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError(
                f"source must be an http(s) URL a reader can open, got {value!r} — "
                "a finding with no verifiable source is not stored"
            )
        return url

    @field_validator("claim", "quote", "source_title")
    @classmethod
    def _single_line(cls, value: str) -> str:
        """Findings render one per line and are re-parsed from that
        rendering (the preview-is-what-is-saved gate); an embedded newline
        would split one finding into two on the way back in."""
        return " ".join(value.split())


class CompanyDossier(BaseModel):
    """A company's stored open-web deep dive (#350) — findings, not prose.

    Rebuilt rather than versioned, the same lifecycle as a POV card and a
    PersonDossier: one dossier per company, replaced when re-run.
    """

    dossier_id: str = Field(default_factory=lambda: str(uuid4()))
    company_key: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    findings: list[CompanyFinding] = Field(min_length=1)
    provider: str = ""
    model: str = ""
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def by_dimension(self, dimension: CompanyDimension) -> list[CompanyFinding]:
        return [finding for finding in self.findings if finding.dimension is dimension]
