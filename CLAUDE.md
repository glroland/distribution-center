# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A demo of an AI-driven distribution center: an A2A agent ingests a purchase
order PDF, extracts it with an LLM, checks/fulfills inventory via a virtual
picking robot, ships whatever was retrieved, and escalates to a human
supervisor when something's short or unknown. Seven independent Python
services (each its own venv/requirements/Containerfile) plus a PDF generator
for demo data, wired together over HTTP/MCP and run either individually or
all at once via the root `Makefile`.

## Services

| Service | Port | Role |
|---|---|---|
| `po-ingest-api` | 8000 | PDF -> Markdown/JSON via Docling |
| `local-dc-agent` | 9100 | The A2A agent: orchestrates extraction + fulfillment |
| `local-wms-api` | 8001 | In-memory inventory ledger for one virtual DC |
| `local-inventory-robot-api` | 8002 | Simulated picking robot on a 2D shelf grid |
| `supervisor-api` | 8003 | Human-in-the-loop escalation queue |
| `local-shipping-api` | 8004 | Mock carrier handoff (tracking numbers, no real carrier) |
| `dashboard-api` | 8090 | Backend-for-frontend + static UI that drives/watches a demo run |
| `test-po-generator` | - | CLI, not a server; generates sample PO PDFs |

Read a service's own `README.md` before working in it — each documents its
REST endpoints, MCP tools, and env vars in detail; this file only covers
what you'd otherwise have to piece together by reading multiple services.

## Common commands

Run from the repo root unless noted. `make help` lists all targets.

```bash
make install                 # pip/uv install every requirements.txt in the repo
make start-all                # start every service in the background (logs: target/logs/, pids: target/pids/)
make status-all                # show which start-all services are up
make kill-all / make restart-all
make generate-pos ARGS="--count 25"   # generate sample PO PDFs into target/pos/
make run-<service>            # run one service in the foreground, e.g. make run-local-dc-agent
```

All `make` targets load env vars from the repo-root `.env` (falling back to
`.env.example`) — not each service's own `.env`. Copy `.env.example` to
`.env` at the root and fill in `OPENAI_API_KEY` before running the agent for
real.

Per-service, once inside its directory with its venv active:

```bash
python -m src            # run that service directly (reads PORT etc. from its own env/.env)
pytest                   # run that service's tests
pytest tests/test_foo.py::test_bar -q   # run a single test
```

There is no repo-wide lint/format/type-check config (no `pyproject.toml`,
`ruff`, `flake8`, or `mypy` setup) and no top-level test runner — testing is
always per-service via `pytest` in that service's own venv.

## Architecture

### Per-service shape

Every backend service (`po-ingest-api`, `local-wms-api`,
`local-inventory-robot-api`, `supervisor-api`, `local-shipping-api`) follows
the same internal layout:

- `src/app.py` — FastAPI app; builds an MCP server via `mcp_server.py` and
  mounts it at `/mcp` in the *same process*, sharing the same in-memory
  store/state as the REST endpoints (see the `lifespan`/`AsyncExitStack`
  wiring in `app.py` — the MCP app's own lifespan has to be entered inside
  FastAPI's).
- `src/mcp_server.py` — MCP tools, deliberately coarser-grained than the
  REST API (e.g. one `adjust_inventory(sku, delta)` tool instead of separate
  increment/decrement endpoints) since these are meant for LLM tool-calling.
  Each server's `MCPServer(..., instructions=...)` string is authoritative
  domain documentation (capacity limits, dock rules, carrier behavior) that
  `local-dc-agent` pulls into its own system prompt at runtime rather than
  duplicating.
- `src/<domain>.py` (`inventory.py`, `robot.py`, `shipping.py`, `store.py`)
  — the actual business logic and in-memory state, seeded from a CSV
  (`local-wms-api`, `local-inventory-robot-api`) where applicable. All state
  is process-local and lost on restart; each service has a reset
  endpoint/tool to reload from the seed CSV.
- `src/settings.py` — `pydantic_settings.BaseSettings`, reads `.env` with
  env vars taking precedence.
- `src/__main__.py` — `configure_logging` then `uvicorn.run("src.app:app", ...)`.

`local-dc-agent` is the exception: it's an A2A agent, not a plain
CRUD-over-MCP service, and connects to the other five as an MCP *client*
rather than hosting its own tools. See "The dc-agent pipeline" below.

`dashboard-api` is also different: it owns no business state, just
orchestrates calls to the other services and serves a static UI
(`src/static/`).

### The dc-agent pipeline (`local-dc-agent`)

Incoming A2A messages are not processed inline on the HTTP request. A single
background `OrderWorker` (`src/worker.py`) owns one persistent set of MCP
connections and processes purchase orders serially off an `asyncio.Queue` —
serialization matters here because there's only one virtual picking robot,
so two orders processing concurrently would race its position.

```
A2A message (PDF)
  -> agent_executor.execute()
       -> worker.submit(pdf, filename)            # enqueue, await the result
            OrderWorker._run() [background task]
              loop: job = await queue.get()        # idle here between orders
                -> po-ingest-api /convert                    (no LLM)
                -> order_extraction.extract_order()           [LLM: forced tool call]
                -> order_processing.process_order()           (subtotal/mismatch)
                -> fulfillment.fulfill_order()                 [LLM tool-calling loop]
                     tools = local-wms-api + local-inventory-robot-api +
                             local-shipping-api + supervisor-api (via MCP)
```

Key detail: the fulfillment loop connects to all four downstream MCP servers
once (`src/mcp_tools.py`) and reuses those connections for the worker's
lifetime, with tool names prefixed by service (`wms__adjust_inventory`,
`robot__plan_and_fetch_items`, `shipping__ship_order`,
`supervisor__request_help`) so one OpenAI tool-calling loop can drive all
four. The robot side is a single coarse-grained call: `plan_and_fetch_items`
takes a full `{sku, qty}` list, and the robot works out visiting order,
movement, capacity-driven dock round-trips, and delivery on its own rather
than the LLM driving `move_robot`/`fetch_item` turn by turn. What it reports
as *fetched_qty* per SKU — not the originally requested quantity — is what
gets shipped and what decrements the WMS ledger. The loop finishes by
calling a `record_fulfillment_result` tool with a structured per-item
summary; if it stalls past `MAX_FULFILLMENT_TURNS`, the agent escalates to
the supervisor directly and returns a degraded result instead of hanging.

Results come back as an A2A artifact: a `DataPart` with the full structured
`ProcessOrderResult` JSON (including the `fulfillment` block) plus a
human-readable `TextPart` summary.

### Live progress via webhook, not streaming (`dashboard-api`)

The dc-agent's `message/send` is a single blocking A2A call with
`capabilities.streaming=False`. To watch a PO move through the pipeline
live, `dashboard-api` passes a `progress_webhook` URL in the outbound
message's `metadata`. If present, the agent POSTs an event to it after
ingest, after extraction, after totals processing, after *every* MCP tool
call in the fulfillment loop (tool name, args, raw result), and once more
with the final result. `dashboard-api` fans these out to the browser over
SSE (`GET /api/runs/{run_id}/stream`) and derives all UI state (inventory
deltas, robot position, shipments, escalations) by parsing each tool call's
result according to which service it came from — no polling during an
active run. The webhook is best-effort: if unreachable, PO processing is
unaffected. Between runs / on initial load, the dashboard falls back to
plain REST polling of each service.

Resolving a help request via the dashboard/supervisor-api does *not*
retroactively resume the PO that raised it — escalation is fire-and-forget
by design so one bad line item never blocks the rest of an order.

### Shared demo data

`products.csv` (repo root) is the product catalog `test-po-generator` draws
line items from; `local-wms-api` and `local-inventory-robot-api` seed
on-hand/shelf stock for most of the same SKUs from their own
`data/inventory.csv` / `data/shelves.csv`. The overlap is intentionally
partial — `local-wms-api`'s seed data only stocks a handful of SKUs (one at
zero on hand), so a generated PO will often exercise both the
pick-and-ship path and the supervisor-escalation path in the same run.

### Deployment (`deploy/`)

`deploy/helm` is a Helm chart with subcharts under `charts/` for
`poIngestApi` and `supervisorApi` (cluster-shared singletons) and
`distributionCenter` (one instance per DC — dc-agent, wms, robot, shipping
together). `values.yaml`'s `global` section is the only values shared
automatically across subcharts (e.g. `global.poIngestApi.port` so a
`distributionCenter` subchart's dc-agent can address the shared ingest
service). `dashboard-api/src/settings.py`'s `DISTRIBUTION_CENTERS` list
mirrors `values.yaml`'s `distributionCenters` list and must be kept in sync
by hand when adding a DC. `deploy/Jenkinsfile` builds/archives a Docker
image per service.
