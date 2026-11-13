import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile

from .conversion import convert_pdf
from .mcp_registration import configure_mcp_registration
from .mcp_server import mcp_server
from .models import ConversionResult
from .settings import settings
from .tracing import configure_tracing

logger = logging.getLogger(__name__)

configure_tracing()
_MCP_MAX_REQUEST_BODY_BYTES = 64 * 1024 * 1024
# Stateless: no Mcp-Session-Id, no per-client transport kept between requests,
# and a plain JSON response per POST rather than an SSE stream -- every
# request stands alone, so any replica can serve any call and a restart
# strands no client. Clients speaking the 2026-07-28 protocol revision are
# session-less by definition; stateless_http extends the same treatment to
# handshake-era (2025) clients instead of opening a session for them.
mcp_app = mcp_server.streamable_http_app(
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
    host=settings.HOST,
    # convert_pdf_to_markdown takes the whole PDF base64-encoded in its
    # arguments; the SDK's default 4 MiB request cap would reject any PDF
    # over ~3 MB before the tool ever ran.
    max_request_body_size=_MCP_MAX_REQUEST_BODY_BYTES,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
        await configure_mcp_registration()
        yield


app = FastAPI(title="PO Ingest API", lifespan=lifespan)
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
