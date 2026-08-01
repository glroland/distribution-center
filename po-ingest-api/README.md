# PO Ingest API

A small FastAPI service that ingests a PDF (e.g. a purchase order produced by
`test-po-generator`) and converts it with
[Docling](https://github.com/docling-project/docling) into both Markdown and
Docling's structured JSON document representation, returned together in a
single response.

## Setup

```bash
cd po-ingest-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# for running tests
pip install -r requirements-dev.txt
```

## Run

```bash
python -m src
```

Starts the API on `http://0.0.0.0:8000`. Interactive docs are available at
`http://localhost:8000/docs`.

> Docling downloads its layout/OCR models the first time it runs a
> conversion. The first request (or first test run) will be slow and needs
> network access; subsequent runs use the cached models.

## Usage

```bash
curl -F "file=@sample.pdf" http://localhost:8000/convert
```

Response:

```json
{
  "filename": "sample.pdf",
  "markdown": "# Purchase Order 1001\n...",
  "document": { "...": "DoclingDocument JSON" }
}
```

| Field | Type | Description |
|---|---|---|
| `filename` | string | Original uploaded filename |
| `markdown` | string | Docling's Markdown export of the document |
| `document` | object | Docling's structured `DoclingDocument` JSON export |

A non-PDF upload or empty file returns `400`. A file Docling fails to parse
returns `422` with the error detail.

## MCP

The service also exposes an MCP server (Streamable HTTP transport) at
`http://localhost:8000/mcp`, in the same process as the REST API, sharing the
same Docling model instance. It offers one tool:

| Tool | Args | Returns |
|---|---|---|
| `convert_pdf_to_markdown` | `pdf_base64: str`, `filename: str = "document.pdf"` | Markdown string |

`pdf_base64` is the PDF's bytes, base64-encoded (MCP tool arguments are
JSON, so there's no raw binary type). Connect with any MCP client that
supports Streamable HTTP, e.g. the `mcp` Python SDK's
`mcp.client.streamable_http.streamable_http_client`.

## Tests

```bash
pytest
```

Builds a small PDF in-memory with `reportlab`, posts it to `/convert`, and
checks the response shape; also calls the `convert_pdf_to_markdown` MCP tool
directly. Downloads Docling's models on first run (see note above).
