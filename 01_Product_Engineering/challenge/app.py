"""The server."""

import base64
import binascii
import os
from pathlib import Path

import gradio as gr
import pymupdf
from fastapi.responses import HTMLResponse
from gradio import Server

import schema
from llm import extract_record, stream_reply

app = Server()

FRONTEND = Path(__file__).parent / "frontend" / "index.html"

MAX_SCAN_BYTES = 10 * 1024 * 1024  # 10 MB
JPEG_MAGIC = b"\xff\xd8\xff"
PDF_MAGIC = b"%PDF-"
PDF_RENDER_DPI = 200
MAX_PDF_PAGES = 50


# --- The API -----------------------------------------------------------------
# The `-> str` return annotation is REQUIRED on every endpoint.


@app.api(name="chat")
def chat(message: str, history: list | None = None) -> str:
    """Stream a reply to one user message."""
    yield from stream_reply(message, history)


@app.api(name="extract_timesheet_record")
def extract_timesheet_record(scan_base64: str, page: int = 1) -> str:
    """Extract payroll fields from a scanned, handwritten payroll timesheet page."""
    image = _to_jpeg(_decode_base64(scan_base64), page)
    request = schema.Request(scan=image, media_type="image/jpeg")
    try:
        response = extract_record(request)
    except ValueError as err:
        raise gr.Error(str(err))
    return response.model_dump_json()


def _decode_base64(scan_base64: str) -> bytes:
    """Base64 text -> raw bytes, with size validation."""
    if scan_base64.startswith("data:"):
        scan_base64 = scan_base64.split(",", 1)[-1]
    try:
        data = base64.b64decode(scan_base64, validate=True)
    except (binascii.Error, ValueError):
        raise gr.Error("scan_base64 is not valid base64.")
    if len(data) > MAX_SCAN_BYTES:
        raise gr.Error("Scan is larger than 10 MB.")
    return data


def _to_jpeg(data: bytes, page: int) -> bytes:
    """Return JPEG bytes. JPEGs pass through; PDFs are rendered one page."""
    if data.startswith(JPEG_MAGIC):
        return data
    if data.startswith(PDF_MAGIC):
        return _pdf_page_to_jpeg(data, page)
    raise gr.Error("Scan must be a JPEG or a PDF.")


def _pdf_page_to_jpeg(pdf_bytes: bytes, page: int) -> bytes:
    """Render one PDF page (1-based) to JPEG at PDF_RENDER_DPI."""
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        raise gr.Error("The PDF could not be opened.")
    try:
        if doc.needs_pass:
            raise gr.Error("The PDF is password protected.")
        if doc.page_count > MAX_PDF_PAGES:
            raise gr.Error(f"The PDF has more than {MAX_PDF_PAGES} pages.")
        if not 1 <= page <= doc.page_count:
            raise gr.Error(
                f"page must be between 1 and {doc.page_count} for this PDF."
            )
        pix = doc[page - 1].get_pixmap(dpi=PDF_RENDER_DPI, alpha=False)
        return pix.tobytes("jpeg", jpg_quality=90)
    finally:
        doc.close()


# --- The frontend ------------------------------------------------------------
# Defining a route at "/" replaces Gradio's default UI with ours.


@app.get("/", response_class=HTMLResponse)
def homepage() -> str:
    return FRONTEND.read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict:
    """Load balancers need this."""
    return {"status": "ok", "model": os.getenv("LLM_MODEL", "gpt-4.1-mini")}


if __name__ == "__main__":
    port = int(os.getenv("GRADIO_SERVER_PORT", "7860"))
    print(f"\n  Open http://localhost:{port} in your browser\n", flush=True)
    app.launch(server_name="0.0.0.0", server_port=port)