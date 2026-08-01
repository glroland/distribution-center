import httpx

from .settings import settings


class IngestError(Exception):
    """Raised when po-ingest-api fails to convert a PDF."""


async def convert_pdf_to_markdown(pdf_bytes: bytes, filename: str) -> str:
    """Convert PDF bytes to Markdown via po-ingest-api's /convert endpoint."""
    async with httpx.AsyncClient(base_url=settings.PO_INGEST_API_URL, timeout=60.0) as client:
        try:
            response = await client.post(
                "/convert",
                files={"file": (filename, pdf_bytes, "application/pdf")},
            )
        except httpx.HTTPError as exc:
            raise IngestError(f"Could not reach po-ingest-api: {exc}") from exc

    if response.status_code != 200:
        detail = response.text
        try:
            detail = response.json().get("detail", detail)
        except ValueError:
            pass
        raise IngestError(f"po-ingest-api returned {response.status_code}: {detail}")

    return response.json()["markdown"]
