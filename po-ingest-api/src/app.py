from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile

from .conversion import convert_pdf
from .mcp_server import mcp_server
from .models import ConversionResult

mcp_app = mcp_server.streamable_http_app(streamable_http_path="/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
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
        raise HTTPException(status_code=400, detail="Uploaded file must be a PDF")

    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    try:
        markdown, document = convert_pdf(data, file.filename or "document.pdf")
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"Failed to convert PDF: {exc}"
        ) from exc

    return ConversionResult(
        filename=file.filename or "document.pdf", markdown=markdown, document=document
    )
