"""Client for po-ingest-api's /convert endpoint. Mirrors
local-dc-agent/src/ingest_client.py's contract exactly (same endpoint,
same response field) since the extraction benchmark needs to exercise the
same PDF-to-markdown step dc-agent itself relies on -- not a stand-in for it.
"""

import httpx

from .settings import settings


class IngestError(Exception):
    """Raised when po-ingest-api fails to convert a PDF."""


async def convert_pdf_to_markdown(pdf_bytes: bytes, filename: str) -> str:
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
