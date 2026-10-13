"""Bounded client file payloads; no client-supplied server filesystem access."""

from __future__ import annotations

import base64
import binascii
import io
import re
import zipfile
import zlib
from datetime import UTC, datetime
from pathlib import Path

from wingman.infrastructure.config import Config

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000


def decode_file(content_base64: str, filename: str, suffixes: set[str]) -> tuple[str, bytes]:
    """Validate before any storage/model work, including compressed expansion limits."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name) > 180 or re.search(r"[\x00-\x1f\x7f]", name):
        raise ValueError(
            "Use a nonempty filename of at most 180 characters, without control characters."
        )
    suffix = Path(name).suffix.lower()
    if suffix not in suffixes:
        raise ValueError("Unsupported file type. Supported: " + ", ".join(sorted(suffixes)))
    # Clients often wrap base64 at 76 columns (MIME, `base64.encodebytes`);
    # whitespace is never part of the payload, so drop it before measuring.
    content_base64 = "".join(content_base64.split())
    if len(content_base64) > 4 * ((MAX_FILE_BYTES + 2) // 3):
        raise ValueError("File exceeds the 10 MiB limit; send a smaller file.")
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(
            "Send the file bytes as standard base64, without a data URL prefix."
        ) from exc
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise ValueError("File must be nonempty and at most 10 MiB.")
    if suffix in {".zip", ".docx"}:
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if (
                    len(entries) > MAX_ARCHIVE_ENTRIES
                    or sum(e.file_size for e in entries) > MAX_ARCHIVE_BYTES
                ):
                    raise ValueError(
                        "Archive exceeds 1000 entries or 50 MiB expanded; send a smaller export."
                    )
                if any(
                    e.flag_bits & 1
                    or e.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    for e in entries
                ):
                    raise ValueError("Use an unencrypted ZIP with stored or deflated entries.")
                # Verify CRC and declared sizes within the bounded archive before application reads.
                if archive.testzip() is not None:
                    raise ValueError("Archive is damaged; re-export and retry.")
        except (
            zipfile.BadZipFile,
            zlib.error,
            EOFError,
            OSError,
            RuntimeError,
            NotImplementedError,
        ) as exc:
            raise ValueError("Archive could not be read; re-export and retry.") from exc
    return name, raw


def discard_upload(path: Path) -> None:
    """Remove an inbox copy whose content was rejected, so retries do not pile
    up private copies of files that imported nothing. Best effort."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def rejection_reason(exc: Exception, path: Path, name: str) -> str:
    """An IngestError's own explanation, with the stamped inbox path replaced by
    the filename the client sent (the internal path is not theirs to see)."""
    return str(exc).replace(str(path), name).replace(path.name, name)


def archive_file(name: str, raw: bytes, config: Config) -> Path:
    """Create an immutable inbox copy, keeping the basename for document lineage."""
    config.inbox_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    path = config.inbox_dir / f"{stamp}-{name}"
    with path.open("xb") as output:
        output.write(raw)
    return path
