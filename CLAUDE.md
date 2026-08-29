# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A demo of an AI-driven distribution center: an A2A agent ingests a purchase
order PDF, extracts it with an LLM, checks/fulfills inventory via a virtual
picking robot, ships whatever was retrieved, and escalates to a human
supervisor when something's short or unknown. Eight independent Python
services (each its own venv/requirements/Containerfile) plus a PDF generator
for demo data and an EvalHub benchmark suite, wired together over HTTP/MCP
and run either individually or all at once via the root `Makefile`.

## Services

| Service | Port | Role |
|---|---|---|
| `po-ingest-api` | 8000 | PDF -> Markdown/JSON via Docling |
| `local-dc-agent` | 9100 | The A2A agent: orchestrates extraction + fulfillment |
| `local-wms-api` | 8001 | In-memory inventory ledger for one virtual DC |
| `local-inventory-robot-api` | 8002 | Simulated picking robot on a 2D shelf grid |
| `supervisor-api` | 8003 | Human-in-the-loop escalation queue |
| `local-shipping-api` | 8004 | Mock carrier handoff (tracking numbers, no real carrier) |
| `label-api` | 8005 | Synthesizes low-quality camera-style photos of SKU stickers; also serves local SKU inference against vision-ml-trained models |
| `dashboard-ui` | 8090 | Backend-for-frontend + static UI that drives/watches a demo run |
| `test-po-generator` | - | CLI, not a server; generates sample PO PDFs |
| `eval-suite` | - | CLI, not a server; EvalHub BYOF benchmarks (extraction accuracy, MCP trajectory, end-to-end outcome) |

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

`dashboard-ui` is also different: it owns no business state, just
orchestrates calls to the other services and serves a static UI
(`src/static/`).

`label-api` (renamed from `label-generator-api`) is also different: it's two
standalone utilities in one service (no LLM, no MLflow tracing).
`src/stickers.py` renders a synthetic camera photo of a white SKU sticker
with PIL/numpy, and `src/bulk.py` batches that into a zip on request —
nothing else in the repo calls this half via REST directly (see below for
its MCP-exposed counterpart, `get_item_photo`). `src/inference.py` is the
other half: it loads the 3 PyTorch checkpoints the `vision-ml` project
trains (localize -> orient -> OCR) from `models/`, bundled into this
service's own Docker image at build time, and runs them in-process against
a sticker photo — a predicted SKU + confidence score, computed locally
rather than by calling out to a shared inference service or model server.
Unlike `po-ingest-api`/`local-wms-api`/etc., `label-api` exposes only one
MCP tool (`infer_sku`, wrapping `src/inference.py`) alongside its REST
surface (`POST /infer` does the same thing over multipart upload, for
non-agentic callers) — the sticker-generation half stays REST-only, called
by `local-inventory-robot-api`'s `get_item_photo` MCP tool and by
`dashboard-ui`'s UI-preview proxy, neither of which goes through MCP.

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
                             local-shipping-api + supervisor-api +
                             label-api (via MCP)
```

Key detail: the fulfillment loop connects to all five downstream MCP servers
once (`src/mcp_tools.py`) and reuses those connections for the worker's
lifetime, with tool names prefixed by service (`wms__adjust_inventory`,
`robot__plan_and_fetch_items`, `robot__get_item_photo`, `label__infer_sku`,
`shipping__ship_order`, `supervisor__request_help`) so one OpenAI
tool-calling loop can drive all five. The robot side is a single
coarse-grained call: `plan_and_fetch_items` takes a full `{sku, qty}` list,
and the robot works out visiting order, movement, capacity-driven dock
round-trips, and delivery on its own rather than the LLM driving
`move_robot`/`fetch_item` turn by turn. What it reports as *fetched_qty* per
SKU — not the originally requested quantity — is what's eligible to ship and
decrement the WMS ledger, but only after a visual-verification step: the
policy prompt (`fulfillment.py`'s `_POLICY_PROMPT`) requires calling
`robot__get_item_photo` then `label__infer_sku` for every SKU actually
fetched before shipping or decrementing it, and treats a returned SKU that
doesn't match what was requested (or a low-confidence read) as a shortfall —
same escalation path as an out-of-stock SKU — rather than shipping on trust
that the shelf was labeled correctly. This is deliberately prompt-driven
(the LLM decides to make these calls, per its system prompt) rather than
hardcoded orchestration, so it stays a genuine agentic step rather than
Python code silently doing the checking. The loop finishes by calling a
`record_fulfillment_result` tool with a structured per-item summary; if it
stalls past `MAX_FULFILLMENT_TURNS`, the agent escalates to the supervisor
directly and returns a degraded result instead of hanging.

Results come back as an A2A artifact: a `DataPart` with the full structured
`ProcessOrderResult` JSON (including the `fulfillment` block) plus a
human-readable `TextPart` summary.

### Live progress via webhook, not streaming (`dashboard-ui`)

The dc-agent's `message/send` is a single blocking A2A call with
`capabilities.streaming=False`. To watch a PO move through the pipeline
live, `dashboard-ui` passes a `progress_webhook` URL in the outbound
message's `metadata`. If present, the agent POSTs an event to it after
ingest, after extraction, after totals processing, after *every* MCP tool
call in the fulfillment loop (tool name, args, raw result), and once more
with the final result. `dashboard-ui` fans these out to the browser over
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

### Evaluation (`eval-suite`)

CLI-shaped like `test-po-generator`, not a server. Three EvalHub BYOF
("bring your own framework") benchmarks, each with a plain `run_local()`
function usable with no EvalHub installation (`cd eval-suite && python -m
src --adapter all`, or `make eval-suite`) plus a `FrameworkAdapter` subclass
(`src/adapters/base.py`, importing `evalhub.adapter` only when present) for
registering the same logic with a real EvalHub instance via
`config/evalhub.yaml`'s weighted collection
(`distribution-center-eval-v1`):

- **`dc-extraction-accuracy`** (`src/adapters/extraction_adapter.py`) —
  calls the *same* prompt (MLflow Prompt Registry or local catalog, per
  `PROMPT_SOURCE`) and tool schema `local-dc-agent/src/order_extraction.py`
  uses, hand-mirrored rather than cross-imported since services here don't
  share a Python package (see "Per-service shape" above), against golden PO
  PDFs generated with exactly-known fields. Meant to gate promoting a new
  `dc-agent.order_extraction.system_prompt` version before
  `PROMPT_SOURCE=mlflow` lets it into production.
- **`dc-mcp-trajectory`** (`src/adapters/mcp_trajectory_adapter.py`) — runs
  real POs through `local-dc-agent` over A2A with a `progress_webhook`
  pointed at a local receiver (`src/webhook_receiver.py`), then validates
  the captured tool-call stream against the five downstream MCP servers'
  *live* `inputSchema`s (fetched at run time, not hardcoded) and checks that
  every stock decrement was preceded by a `robot__get_item_photo` ->
  `label__infer_sku` pair, per the fulfillment policy prompt's
  visual-verification requirement.
- **`dc-end-to-end`** (`src/adapters/end_to_end_adapter.py`) — submits POs
  whose stock-availability outcome is precomputed from
  `local-wms-api/data/inventory.csv` and
  `local-inventory-robot-api/data/shelves.csv` (`src/seed_data.py`), and
  scores the agent's actual `fulfilled_qty`/`order_status` against that
  ground truth. Deliberately rules-based, not an LLM judge — the correct
  answer here is a computable fact, not a matter of taste.

PO PDFs for all three benchmarks are rendered by `src/pdf_builder.py`, a
minimal ReportLab renderer kept independent of `test-po-generator`'s (whose
`src`-named package can't be cross-imported without collision, and whose
random sampling can't be steered to specific known-outcome SKUs anyway).

### Deployment (`deploy/`)

`deploy/helm` is a Helm chart with subcharts under `charts/` for
`poIngestApi`, `supervisorApi`, `labelApi`, and `dashboardUi`
(cluster-shared singletons) and `dcAgent`, `wmsApi`, `robotApi`, and
`shippingApi` (the single DC's components — each its own subchart, wired
only to each other plus the singletons above). `values.yaml`'s `global`
section is the only values shared automatically across subcharts (e.g.
`global.poIngestApi.port` so `dcAgent` can address the shared ingest
service, and likewise `global.wmsApi/robotApi/shippingApi.port` so
`dcAgent` can address its now-separate DC siblings). Locally,
`dashboard-ui/src/settings.py`'s hardcoded `DISTRIBUTION_CENTER` mirrors
the `dcAgent`/`wmsApi`/`robotApi`/`shippingApi` blocks in `values.yaml` by
hand; in the chart, `dashboardUi.distributionCenter` (itself hand-mirroring
those same blocks — Helm only shares `global` values across subcharts) is
rendered into a `DISTRIBUTION_CENTER_JSON` env var that `settings.py`
parses instead, pointing at the DC's in-cluster Service DNS rather than
`localhost`. Both mirrors must be kept in sync by hand. `deploy/Jenkinsfile`
builds/archives a Docker image per service.
