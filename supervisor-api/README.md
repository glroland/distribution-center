# Local Supervisor API

A small demo API that lets an AI agent escalate to a human supervisor when it
gets stuck. Help requests are held entirely in memory in a list - they are
lost on restart. Agents raise requests through an MCP tool; a supervisor
reviews and resolves them through the REST API.

## Setup

```bash
cd supervisor-api
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

Starts the API on `http://0.0.0.0:8003`. Interactive docs are available at
`http://localhost:8003/docs`.

Settings are defined in [`src/settings.py`](src/settings.py) with reasonable
defaults; copy `.env.example` to `.env` and fill it in to override them, or
set environment variables directly (env vars take precedence over `.env`).

| Env var | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `8003` | Bind port |

## REST API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/help-requests` | List help requests, optionally filtered with `?status=open` or `?status=resolved` |
| `GET` | `/help-requests/{id}` | Get a single help request (404 if unknown) |
| `POST` | `/help-requests/{id}/resolve` | Body `{"resolution": str}`; mark a request resolved (404 if unknown, 400 if already resolved) |

```bash
curl -X POST http://localhost:8003/help-requests/1/resolve \
  -H 'Content-Type: application/json' -d '{"resolution": "Use bin A3 for this SKU."}'
```

## MCP

The service also exposes an MCP server (Streamable HTTP transport) at
`http://localhost:8003/mcp`, in the same process as the REST API, sharing the
same in-memory store. There is a single tool for agents to raise a help
request; listing and resolving are supervisor-side actions and are only
exposed through the REST API.

| Tool | Args | Description |
|---|---|---|
| `request_help` | `question: str`, `agent_id: str \| None`, `context: str \| None` | Queue a help request, initially `open` |

Connect with any MCP client that supports Streamable HTTP, e.g. the `mcp`
Python SDK's `mcp.client.streamable_http.streamable_http_client`.

## Tests

```bash
pytest
```

Covers the in-memory store, the REST API, and the MCP tool.
