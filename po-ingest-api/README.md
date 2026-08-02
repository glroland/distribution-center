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

Settings are defined in [`src/settings.py`](src/settings.py) with
reasonable defaults; copy `.env.example` to `.env` and fill it in to
override them, or set environment variables directly (env vars take
precedence over `.env`).

| Env var | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `8000` | Bind port |
| `MLFLOW_TRACKING_URI` | unset | MLflow tracking server URL. When set, the `convert_pdf_to_markdown` MCP tool call is traced as an MLflow span (see [`src/tracing.py`](src/tracing.py)); left unset, tracing is disabled outright. MLflow's own env vars (`MLFLOW_EXPERIMENT_NAME`, `MLFLOW_WORKSPACE`, `MLFLOW_TRACKING_TOKEN`, `MLFLOW_TRACKING_AUTH`, ...) are read natively by the `mlflow` package alongside this one |

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
supports Streamable HTTP, e.g. `fastmcp.Client`.

## Tests

```bash
pytest
```

Builds a small PDF in-memory with `reportlab`, posts it to `/convert`, and
checks the response shape; also calls the `convert_pdf_to_markdown` MCP tool
directly. Downloads Docling's models on first run (see note above).
