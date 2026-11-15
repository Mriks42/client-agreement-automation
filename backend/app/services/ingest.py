"""Turn an uploaded agreement into a normalized PDF (DECISIONS.md D2 step 1, D3).

Uploads are untrusted: the type is decided by magic bytes, never by filename or Content-Type,
and size and page limits are enforced before any heavy work. DOCX is converted with LibreOffice
headless, one conversion at a time (the free tier has 0.1 CPU; DECISIONS.md D10).
"""

import asyncio
import hashlib
import io
import logging
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.services import pdf_engine

log = logging.getLogger(__name__)

SourceFormat = Literal["pdf", "docx"]

CONVERT_TIMEOUT_S = 120
_MAX_DOCX_UNCOMPRESSED = 200 * 1024 * 1024  # zip-bomb guard
_convert_lock = asyncio.Lock()


class IngestError(Exception):
    """An upload we can't use. `message` is shown to the user and says what to do next."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class IngestResult:
    pdf: bytes
    source_format: SourceFormat
    page_count: int
    source_sha256: str
    convert_ms: int | None = None


def sniff(data: bytes) -> SourceFormat:
    if not data:
        raise IngestError("empty", "The file is empty.")
    if b"%PDF-" in data[:1024]:
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
                total = sum(i.file_size for i in zf.infolist())
        except zipfile.BadZipFile as exc:
            raise IngestError("unreadable", "The file looks damaged. Try saving it again.") from exc
        if "word/document.xml" in names:
            if total > _MAX_DOCX_UNCOMPRESSED:
                raise IngestError("too_large", "This Word document is too large to process.")
            return "docx"
        if any(n.startswith(("xl/", "ppt/")) for n in names):
            raise IngestError(
                "unsupported_type",
                "That's a spreadsheet or presentation. Upload the agreement as a PDF or Word "
                "(.docx) file.",
            )
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise IngestError(
            "legacy_doc",
            "This is an older Word (.doc) file. Open it in Word and save it as .docx or PDF, "
            "then upload that.",
        )
    raise IngestError("unsupported_type", "Upload the agreement as a PDF or Word (.docx) file.")


async def ingest(data: bytes, settings: Settings) -> IngestResult:
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        raise IngestError(
            "too_large",
            f"The file is larger than {settings.max_upload_mb} MB. Upload a smaller one.",
        )
    fmt = sniff(data)
    convert_ms: int | None = None
    pdf = data
    if fmt == "docx":
        start = time.perf_counter()
        pdf = await docx_to_pdf(data, settings.libreoffice_bin)
        convert_ms = int((time.perf_counter() - start) * 1000)
        log.info("docx converted", extra={"ms": convert_ms, "bytes": len(data)})
    try:
        normalized = await asyncio.to_thread(
            pdf_engine.normalize_pdf, pdf, max_pages=settings.max_pages
        )
    except pdf_engine.PdfError as exc:
        raise IngestError(exc.code, exc.message) from exc
    return IngestResult(
        pdf=normalized.data,
        source_format=fmt,
        page_count=normalized.page_count,
        source_sha256=hashlib.sha256(data).hexdigest(),
        convert_ms=convert_ms,
    )


async def docx_to_pdf(data: bytes, soffice: str, timeout_s: float = CONVERT_TIMEOUT_S) -> bytes:
    fallback = "Upload the agreement as a PDF instead (in Word: File → Save As → PDF)."
    async with _convert_lock:
        with tempfile.TemporaryDirectory(prefix="docx-") as tmp:
            work = Path(tmp)
            src = work / "agreement.docx"  # never use the uploaded filename on disk
            src.write_bytes(data)
            # A private profile per conversion: no lock files left behind by a killed run,
            # and it works whatever user the container runs as.
            profile = (work / "profile").as_uri()
            cmd = [
                soffice,
                f"-env:UserInstallation={profile}",
                "--headless",
                "--norestore",
                "--nolockcheck",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(work),
                str(src),
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
            except FileNotFoundError as exc:
                log.error("libreoffice not installed", extra={"bin": soffice})
                raise IngestError(
                    "conversion_unavailable", f"Word conversion isn't available. {fallback}"
                ) from exc
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except TimeoutError as exc:
                proc.kill()
                await proc.wait()
                log.error("docx conversion timed out", extra={"timeout_s": timeout_s})
                raise IngestError(
                    "conversion_timeout", f"Converting the Word document took too long. {fallback}"
                ) from exc
            out = work / "agreement.pdf"
            if proc.returncode != 0 or not out.exists():
                log.error(
                    "docx conversion failed",
                    extra={
                        "returncode": proc.returncode,
                        "stderr": stderr.decode(errors="replace")[-500:],
                    },
                )
                raise IngestError(
                    "conversion_failed", f"We couldn't convert this Word document. {fallback}"
                )
            return out.read_bytes()
