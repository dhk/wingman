"""Published artifacts: where a rendered view of this workspace ended up.

Wingman renders things a chat window is bad at showing — the values radar,
the completeness report, the profile with its evidence. A Claude client can
publish one of those as an artifact: a page with a stable URL, rendered
in-app rather than behind a browser link.

The client publishes; wingman records where it went. Updating a page in
place needs its URL, and a conversation that did not publish it has no way
to know one — so without this, every regeneration mints another page and
leaves the last one quietly wrong. It is also how a scheduled conversation
refreshes the canonical page rather than adding to the pile.

The workspace stays the system of record. An artifact is a rendering of it,
and this table says nothing about whether that rendering is still true —
`built_from` records the inputs so a caller can decide (issue #355).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

#: Built-in views; client-owned views use the validated custom:<slug> namespace.
#:
#: `values_radar` and `values_radar_work` are the two READINGS of the same
#: captures (`domain.values.ValueView`, issue #356), and they are separate
#: kinds rather than one because "one current page per kind" is what makes
#: "update my values page" resolve without asking: fold them together and
#: publishing the work chart silently replaces the record of where the
#: character chart went, leaving a live page nothing can find again.
ARTIFACT_KINDS = ("values_radar", "values_radar_work", "completeness", "profile")


class PublishedArtifact(BaseModel):
    """One published page, and what it was built from.

    `kind` is the canonical identity: one current page per kind per
    workspace, so "update my progress page" resolves without asking. A
    second publish of the same kind replaces this record rather than
    accumulating, which mirrors what the client does with the URL itself.

    `built_from` is deliberately opaque here — a short fingerprint of the
    inputs, whatever the producing view considers its inputs to be. This
    module refuses to interpret it, because the moment it does, it owns a
    second opinion about staleness that can drift from the view's own.
    """

    kind: str
    url: str
    title: str = ""
    built_from: str = ""
    published_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = ["ARTIFACT_KINDS", "PublishedArtifact"]
