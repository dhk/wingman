"""SourceRecord: the immutable root of every provenance chain."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import PurePath
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# Inbox archives are stamped '{stamp}-{hash8}-{name}' (file copies) or
# '{stamp}-{name}' (URL fetches); the document's own name is what's left.
_STAMPED_NAME = re.compile(r"^\d{8}T\d{6,12}-(?:[0-9a-f]{8}-)?(.+)$")


def derive_document_key(name: str) -> str:
    """The document identity behind a filename or locator (RFC-028).

    Two ingests of 'wingman_source_of_truth.md' are two versions of one
    document, however their inbox archives are stamped. Deterministic and
    lossy on purpose: basename, stamp prefixes stripped, case-folded.
    """
    base = PurePath(name.strip()).name
    match = _STAMPED_NAME.match(base)
    if match:
        base = match.group(1)
    return " ".join(base.lower().split())


class SourceRecord(BaseModel):
    """An imported artifact (resume, job description, export, saved page).

    The raw bytes stay on disk in the workspace inbox; the record stores their
    hash and locator. Immutable once ingested — corrections create new records.
    document_key names the document the record is a version of: records
    sharing it are versions of one evolving artifact (RFC-028), '' means
    lineage is unknown or inapplicable.
    """

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(default_factory=lambda: str(uuid4()))
    source_type: str
    source_locator: str
    content_hash: str
    document_key: str = ""
    source_timestamp: datetime | None = None
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
