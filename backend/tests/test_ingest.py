import io
import shutil
import zipfile

import pymupdf
import pytest
from docx import Document

from app.services.ingest import IngestError, docx_to_pdf, ingest, sniff
from tests.conftest import make_settings


def pdf_bytes(text: str = "Hello") -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def docx_bytes(text: str = "Client name: ______________") -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def zip_with(*names: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in names:
            zf.writestr(n, "x")
    return buf.getvalue()


def test_sniff_uses_magic_bytes_not_names() -> None:
    assert sniff(pdf_bytes()) == "pdf"
    assert sniff(b"\n\n" + pdf_bytes()) == "pdf"  # junk before the header is allowed
    assert sniff(docx_bytes()) == "docx"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "empty"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100, "legacy_doc"),
        (zip_with("[Content_Types].xml", "xl/workbook.xml"), "unsupported_type"),
        (b"PK\x03\x04 not really a zip", "unreadable"),
        (b"<html>an agreement</html>", "unsupported_type"),
    ],
)
def test_sniff_rejects_with_a_next_step(data: bytes, code: str) -> None:
    with pytest.raises(IngestError) as err:
        sniff(data)
    assert err.value.code == code
    assert err.value.message


async def test_ingest_enforces_size_limit() -> None:
    settings = make_settings(max_upload_mb=1)
    with pytest.raises(IngestError) as err:
        await ingest(b"%PDF-" + b"0" * (1024 * 1024 + 1), settings)
    assert err.value.code == "too_large"


async def test_ingest_pdf_normalizes_and_hashes_the_original() -> None:
    data = pdf_bytes()
    result = await ingest(data, make_settings())
    assert result.source_format == "pdf" and result.page_count == 1
    assert len(result.source_sha256) == 64
    assert result.pdf.startswith(b"%PDF-")


async def test_docx_without_libreoffice_says_to_upload_a_pdf() -> None:
    settings = make_settings(libreoffice_bin="/nonexistent/soffice")
    with pytest.raises(IngestError) as err:
        await ingest(docx_bytes(), settings)
    assert err.value.code == "conversion_unavailable"
    assert "PDF" in err.value.message


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
async def test_docx_converts_to_pdf() -> None:
    pdf = await docx_to_pdf(docx_bytes(), "soffice")
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        assert "Client name" in doc[0].get_text()
