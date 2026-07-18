"""Resume format extraction: PDF, DOCX, LaTeX, Google URLs — all deterministic."""

import io
import zipfile
from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.resume_formats import (
    _google_export,
    docx_text,
    extract_resume_text,
    fetch_resume_bytes,
    latex_text,
    pdf_text,
    sniff_resume_text,
    suffix_for_bytes,
)


def minimal_pdf(text: str) -> bytes:
    """Assemble a one-page PDF with a correct xref table."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        None,  # content stream, built below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects[3] = (
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
    )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")  # type: ignore[operator]
    xref_at = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF".encode()
    )
    return out.getvalue()


def minimal_docx(paragraphs: list[str]) -> bytes:
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    document = f'<?xml version="1.0"?><w:document {ns}><w:body>{body}</w:body></w:document>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", document)
    return out.getvalue()


LATEX_RESUME = r"""
\documentclass{moderncv}
\name{Jo}{Doe} % a comment about the preamble
\begin{document}
\section{Experience}
\cventry{2020}{Staff Engineer}{DataCo}{Remote}{}{Shipped the search rewrite \& scaled it 10x.}
\begin{itemize}
\item Led streaming pipelines % trailing comment
\item Wrote 50\% fewer incidents
\end{itemize}
Skills: Python \textbf{Kafka}
\end{document}
"""


def test_pdf_roundtrip_and_scan_failure() -> None:
    text = pdf_text(minimal_pdf("Staff engineer at DataCo"), "resume.pdf")
    assert "Staff engineer at DataCo" in text
    with pytest.raises(IngestError, match="no extractable text"):
        pdf_text(minimal_pdf(""), "scan.pdf")
    with pytest.raises(IngestError, match="could not extract"):
        pdf_text(b"%PDF-1.4 garbage", "broken.pdf")


def test_docx_extraction_and_failures() -> None:
    text = docx_text(minimal_docx(["Jo Doe", "Shipped the search rewrite."]), "resume.docx")
    assert text == "Jo Doe\nShipped the search rewrite."
    with pytest.raises(IngestError, match="could not read"):
        docx_text(b"PK\x03\x04 not a real zip", "broken.docx")
    with pytest.raises(IngestError, match="no extractable text"):
        docx_text(minimal_docx([]), "empty.docx")


def test_latex_flattens_to_visible_words() -> None:
    text = latex_text(LATEX_RESUME, "resume.tex")
    assert "Shipped the search rewrite & scaled it 10x." in text
    assert "- Led streaming pipelines" in text
    assert "50% fewer incidents" in text
    assert "Skills: Python Kafka" in text
    assert "\\cventry" not in text and "moderncv" not in text
    assert "a comment about the preamble" not in text
    with pytest.raises(IngestError, match="flattened to nothing"):
        latex_text(r"\documentclass{a}\begin{document}\bigskip ~\end{document}", "empty.tex")


def test_extract_dispatches_on_suffix(tmp_path: Path) -> None:
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(minimal_pdf("From the PDF"))
    assert "From the PDF" in extract_resume_text(pdf)

    docx = tmp_path / "resume.docx"
    docx.write_bytes(minimal_docx(["From the DOCX"]))
    assert "From the DOCX" in extract_resume_text(docx)

    tex = tmp_path / "resume.tex"
    tex.write_text(LATEX_RESUME, encoding="utf-8")
    assert "search rewrite" in extract_resume_text(tex)

    markdown = tmp_path / "resume.md"
    markdown.write_text("# Jo\nplain markdown", encoding="utf-8")
    assert extract_resume_text(markdown) == "# Jo\nplain markdown"

    binary = tmp_path / "resume.md"
    binary.write_bytes(b"\xff\xfe garbage")
    with pytest.raises(IngestError, match="not UTF-8"):
        extract_resume_text(binary)


def test_google_urls_map_to_export_endpoints() -> None:
    doc_id, export = _google_export("https://docs.google.com/document/d/ABC123_x-9/edit?tab=t.0")
    assert doc_id == "ABC123_x-9"
    assert export == "https://docs.google.com/document/d/ABC123_x-9/export?format=txt"

    _, export = _google_export("https://drive.google.com/file/d/FILE42/view?usp=sharing")
    assert export == "https://drive.google.com/uc?export=download&id=FILE42"

    with pytest.raises(IngestError, match="only https"):
        _google_export("http://docs.google.com/document/d/ABC/edit")
    with pytest.raises(IngestError, match="Google Docs"):
        _google_export("https://example.com/resume.pdf")


def test_fetch_resume_bytes_sniffs_and_rejects_permission_walls() -> None:
    fetched: list[str] = []

    def doc_fetcher(url: str) -> bytes:
        fetched.append(url)
        return "Jo Doe\nStaff Engineer".encode()

    name, data = fetch_resume_bytes(
        "https://docs.google.com/document/d/ABC123/edit", fetcher=doc_fetcher
    )
    assert fetched == ["https://docs.google.com/document/d/ABC123/export?format=txt"]
    assert name == "google-ABC123"
    assert sniff_resume_text(data, name) == "Jo Doe\nStaff Engineer"
    assert suffix_for_bytes(data) == ".txt"
    assert suffix_for_bytes(minimal_pdf("x")) == ".pdf"
    assert suffix_for_bytes(minimal_docx(["x"])) == ".docx"

    def login_wall(url: str) -> bytes:
        return b"<!DOCTYPE html><html>Sign in to continue</html>"

    with pytest.raises(IngestError, match="link-accessible"):
        fetch_resume_bytes("https://drive.google.com/file/d/F1/view", fetcher=login_wall)
