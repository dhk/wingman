"""Remembering where a rendered view of this workspace was published.

See `domain.artifacts`: the client publishes, wingman holds the URL so the
next conversation — a scheduled one included — refreshes that page instead
of minting a second one.
"""

from __future__ import annotations

from wingman.application.ingest import IngestError
from wingman.domain.artifacts import ARTIFACT_KINDS, PublishedArtifact
from wingman.infrastructure.storage import Storage


def _valid_kind(kind: str) -> str:
    cleaned = kind.strip().lower()
    if cleaned not in ARTIFACT_KINDS:
        known = ", ".join(ARTIFACT_KINDS)
        raise IngestError(f"unknown artifact kind {kind!r} — expected one of: {known}")
    return cleaned


def _valid_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned.startswith("https://"):
        raise IngestError(f"an artifact url must be https:// — got {url!r}")
    return cleaned


def remember_artifact(
    kind: str, url: str, storage: Storage, title: str = "", built_from: str = ""
) -> PublishedArtifact:
    """Record where `kind` was published, replacing any earlier record."""
    artifact = PublishedArtifact(
        kind=_valid_kind(kind),
        url=_valid_url(url),
        title=title.strip(),
        built_from=built_from.strip(),
    )
    storage.record_published_artifact(artifact)
    return artifact


def published_artifact(kind: str, storage: Storage) -> PublishedArtifact | None:
    return storage.get_published_artifact(_valid_kind(kind))


def forget_artifact(kind: str, storage: Storage) -> bool:
    return storage.forget_published_artifact(_valid_kind(kind))


def render_artifacts(artifacts: list[PublishedArtifact]) -> str:
    """The listing, and the caveat that has to travel with it.

    Every line says when it was published, because this table records where
    a page is and never whether it is still true. A URL with no date reads
    as current, which is exactly the wrong impression for a snapshot of a
    workspace that has moved on (#355).
    """
    if not artifacts:
        return (
            "No published artifacts recorded yet. Publish one from a Claude client, then "
            "record it here so a later conversation can update that same page instead of "
            "creating another."
        )
    lines = ["Published artifacts (wingman records the url; it never publishes anything):"]
    for artifact in artifacts:
        title = f" — {artifact.title}" if artifact.title else ""
        lines.append(
            f"- {artifact.kind}{title}\n"
            f"    {artifact.url}\n"
            f"    published {artifact.published_at.date().isoformat()}"
            + (f", built from {artifact.built_from}" if artifact.built_from else "")
        )
    lines.append(
        "\nThese are snapshots. Nothing here checks whether the workspace has changed since "
        "— rebuild the view and republish to the same url to refresh one."
    )
    return "\n".join(lines)


__all__ = [
    "forget_artifact",
    "published_artifact",
    "remember_artifact",
    "render_artifacts",
]
