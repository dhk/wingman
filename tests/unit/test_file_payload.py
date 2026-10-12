"""Bound payloads before persistent writes or model calls."""

import base64
import io
import zipfile

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
