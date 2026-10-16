"""Bound payloads before persistent writes or model calls."""

import base64
import io
import zipfile
import zlib

import pytest

from wingman.application import file_payload as files


def test_payload_limits(monkeypatch):
    monkeypatch.setattr(files, "MAX_FILE_BYTES", 3)
    assert files.decode_file(base64.b64encode(b"abc").decode(), "a.txt", {".txt"})[1] == b"abc"
    with pytest.raises(ValueError, match="limit|at most"):
        files.decode_file(base64.b64encode(b"abcd").decode(), "a.txt", {".txt"})


def test_archive_limits_before_decompression(monkeypatch):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("big.txt", "x" * 1000)
    data = base64.b64encode(stream.getvalue()).decode()
    monkeypatch.setattr(files, "MAX_ARCHIVE_BYTES", 999)
    with pytest.raises(ValueError, match="expanded"):
        files.decode_file(data, "a.zip", {".zip"})
    monkeypatch.setattr(files, "MAX_ARCHIVE_BYTES", 1000)
    monkeypatch.setattr(files, "MAX_ARCHIVE_ENTRIES", 0)
    with pytest.raises(ValueError, match="entries"):
        files.decode_file(data, "a.zip", {".zip"})


def test_filename_is_only_a_basename():
    assert files.decode_file("eA==", r"C:\private\resume.txt", {".txt"}) == ("resume.txt", b"x")


def _corrupt_deflate_zip() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("a.txt", b"hello world " * 1000)
    raw = bytearray(stream.getvalue())
    # Flip two bytes at the head of the deflate data (30-byte local header + name):
    # the block header becomes invalid, which zlib reports as zlib.error.
    start = 30 + len("a.txt")
    raw[start + 2] ^= 0xFF
    raw[start + 3] ^= 0xFF
    corrupt = bytes(raw)
    with pytest.raises(zlib.error), zipfile.ZipFile(io.BytesIO(corrupt)) as archive:
        archive.testzip()  # the fixture really does reach the zlib.error path
    return corrupt


def test_a_corrupt_deflate_stream_is_a_clean_refusal_not_a_zlib_error():
    """testzip() lets zlib.error escape; a client must never see the raw library message."""
    data = base64.b64encode(_corrupt_deflate_zip()).decode()
    with pytest.raises(ValueError, match="re-export and retry"):
        files.decode_file(data, "linkedin.zip", {".zip"})


def test_line_wrapped_base64_is_accepted():
    """base64.encodebytes / MIME wrap at 76 columns; the payload is the same bytes."""
    raw = b"synthetic resume text " * 20
    wrapped = base64.encodebytes(raw).decode()
    assert "\n" in wrapped
    assert files.decode_file(wrapped, "resume.txt", {".txt"})[1] == raw


@pytest.mark.parametrize("compression", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_unsupported_compression_is_refused(compression):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression) as archive:
        archive.writestr("essay.txt", b"synthetic")
    with pytest.raises(ValueError, match="stored or deflated"):
        files.decode_file(base64.b64encode(stream.getvalue()).decode(), "a.zip", {".zip"})


def test_bad_crc_and_encrypted_entries_are_refused():
    import struct

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("essay.txt", b"synthetic")
    raw = bytearray(stream.getvalue())
    raw[30 + len("essay.txt")] ^= 1
    with pytest.raises(ValueError, match="damaged"):
        files.decode_file(base64.b64encode(raw).decode(), "a.zip", {".zip"})
    raw = bytearray(stream.getvalue())
    central = raw.index(b"PK\x01\x02")
    struct.pack_into("<H", raw, 6, 1)
    struct.pack_into("<H", raw, central + 8, 1)
    with pytest.raises(ValueError, match="unencrypted"):
        files.decode_file(base64.b64encode(raw).decode(), "a.zip", {".zip"})
