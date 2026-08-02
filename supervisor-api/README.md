# Local Supervisor API

A small demo API that lets an AI agent escalate to a human supervisor when it
gets stuck, or try to resolve a stock shortfall itself first by requesting an
inventory transfer from another distribution center. Both help requests and
transfer requests are held entirely in memory in a list - they are lost on
restart. Agents raise them through MCP tools; a supervisor reviews and
resolves help requests through the REST API (transfer requests resolve
immediately, no supervisor involved).

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
| `TRANSFER_UNAVAILABLE_CHANCE` | `0.3333` (1/3) | Odds a `request_transfer` call comes back `unavailable` |

## REST API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/help-requests` | List help requests, optionally filtered with `?status=open` or `?status=resolved` |
| `GET` | `/help-requests/{id}` | Get a single help request (404 if unknown) |
| `POST` | `/help-requests/{id}/resolve` | Body `{"resolution": str}`; mark a request resolved (404 if unknown, 400 if already resolved) |
| `POST` | `/help-requests/reset` | Clear all help requests and transfer requests (test/demo reset) |
| `GET` | `/transfer-requests` | List transfer requests, optionally filtered with `?status=available` or `?status=unavailable` |
| `GET` | `/transfer-requests/{id}` | Get a single transfer request (404 if unknown) |

```bash
curl -X POST http://localhost:8003/help-requests/1/resolve \
  -H 'Content-Type: application/json' -d '{"resolution": "Use bin A3 for this SKU."}'
```

## MCP

The service also exposes an MCP server (Streamable HTTP transport) at
`http://localhost:8003/mcp`, in the same process as the REST API, sharing the
same in-memory store. Listing/resolving help requests and listing transfer
requests are supervisor-side actions and are only exposed through the REST
API.

| Tool | Args | Description |
|---|---|---|
| `request_help` | `question: str`, `agent_id: str \| None`, `context: str \| None` | Queue a help request, initially `open` |
| `request_transfer` | `sku: str`, `quantity: int`, `agent_id: str \| None`, `context: str \| None` | Try to source a shortfall from another DC; resolves immediately with `status` `available` (plus `source_location`) or `unavailable` |

`request_transfer` is meant to be tried before `request_help` when a SKU is
short: most of the time (`1 - TRANSFER_UNAVAILABLE_CHANCE`) it succeeds and
names a `source_location` to ship from; the rest of the time the SKU is
unavailable everywhere else too and the agent should fall back to
`request_help` for a human to sort out.

Connect with any MCP client that supports Streamable HTTP, e.g. the `mcp`
Python SDK's `mcp.client.streamable_http.streamable_http_client`.

## Tests

```bash
pytest
```

Covers the in-memory store, the REST API, and the MCP tool.
