import logging

from fastapi import FastAPI, File, HTTPException, UploadFile

from .conversion import convert_pdf
from .mcp_server import mcp_server
from .models import ConversionResult
from .tracing import configure_tracing

logger = logging.getLogger(__name__)

configure_tracing()
mcp_app = mcp_server.http_app(path="/")

app = FastAPI(title="PO Ingest API", lifespan=mcp_app.lifespan)
app.mount("/mcp", mcp_app)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/convert", response_model=ConversionResult)
def convert(file: UploadFile = File(...)) -> ConversionResult:
    is_pdf = file.content_type == "application/pdf" or (
        file.filename is not None and file.filename.lower().endswith(".pdf")
    )
    if not is_pdf:
        logger.warning("Rejected upload %r: not a PDF (content_type=%s)", file.filename, file.content_type)
        raise HTTPException(status_code=400, detail="Uploaded file must be a PDF")

    data = file.file.read()
    if not data:
        logger.warning("Rejected upload %r: empty file", file.filename)
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    logger.info("Converting %r (%d bytes)", file.filename, len(data))
    try:
        markdown, document = convert_pdf(data, file.filename or "document.pdf")
    except Exception as exc:
        logger.exception("Failed to convert %r", file.filename)
        raise HTTPException(
            status_code=422, detail=f"Failed to convert PDF: {exc}"
        ) from exc

    logger.info("Converted %r into %d chars of markdown", file.filename, len(markdown))
    return ConversionResult(
        filename=file.filename or "document.pdf", markdown=markdown, document=document
    )
