# DC Agent

An [A2A](https://a2a-protocol.org/) (Agent2Agent) agent for a distribution
center. It is intentionally scoped to a single skill for now:

| Skill | Description |
|---|---|
| `process_purchase_order` | Ingests a purchase order PDF, extracts its structured fields, checks and fulfills inventory, ships whatever was retrieved, and returns a processed order record with tracking numbers. |

More distribution center skills are expected to be added later as separate
skills on this same agent — this is not meant to be the last one.

## Architecture

Incoming A2A messages don't run this work inline on the HTTP request. Each
one is handed to a single background `OrderWorker` (`src/worker.py`) that
owns one persistent set of MCP connections and processes purchase orders
serially from an `asyncio.Queue`. The worker's loop blocks on `queue.get()`
between jobs, so no LLM call (or any other work) happens while there's
nothing to process — and because there's only one physical picking robot,
serializing through one worker also avoids two orders racing its position.

```
A2A message (PDF)
  -> agent_executor.execute()
       -> worker.submit(pdf, filename)         # enqueue, await the result
            OrderWorker._run() [background task]
              loop: job = await queue.get()    # idle here between orders
                -> po-ingest-api /convert                (no LLM)
                -> order_extraction.extract_order()       [LLM call: forced tool]
                -> order_processing.process_order()       (subtotal/mismatch)
                -> fulfillment.fulfill_order()             [LLM tool-calling loop]
                     tools = local-wms-api + local-inventory-robot-api +
                             local-shipping-api + supervisor-api +
                             label-api (via MCP)
```

## How `process_purchase_order` works

1. The caller sends an A2A message with a PDF file part (a PO).
2. The PDF is converted to Markdown via
   [`po-ingest-api`](../po-ingest-api) (`PO_INGEST_API_URL`, default
   `http://localhost:8000`) — this agent does not embed Docling itself.
3. The Markdown is sent to OpenAI (`openai` SDK) with a forced tool call
   to extract structured fields: PO number, vendor, buyer, ship-to,
   payment terms, and line items. An LLM is used here (rather than
   per-template parsing) because `test-po-generator` produces a dozen
   visually distinct letterhead layouts, and more may be added.
4. The extracted order is processed: subtotal is recomputed from the line
   items, compared against any stated total (flagging `totals_mismatch` if
   they disagree), and a `dc_order_id` is assigned.
5. A second, tool-calling LLM loop (`src/fulfillment.py`) fulfills the
   order, using MCP tools exposed by five other services in this repo:
   - [`local-wms-api`](../local-wms-api) — check on-hand quantity for each
     SKU before doing anything physical, and decrement it once stock has
     actually been picked and visually verified (see below).
   - [`local-inventory-robot-api`](../local-inventory-robot-api) — call
     `plan_and_fetch_items` once with every SKU/qty needed; the robot works
     out an efficient visiting order, moves, picks, makes extra dock
     round-trips on its own if capacity would otherwise be exceeded, and
     delivers everything at the end. What it reports as `fetched_qty` per
     SKU — not the requested quantity — is what's eligible to be shipped and
     decrement the WMS ledger, once verified. Stock arriving via an approved
     inter-DC transfer is placed on a shelf with `restock_shelf`, then
     picked up with another `plan_and_fetch_items` call the normal way.
     `get_item_photo` captures a photo of a picked SKU's shelf sticker, as if
     by the robot's own camera, for the verification step below.
   - [`label-api`](../label-api) — `infer_sku` reads the SKU printed on a
     photo (e.g. one from `get_item_photo`) locally, against checkpoints
     bundled into that service, and returns it with a confidence score. The
     fulfillment policy chains `robot__get_item_photo` into `label__infer_sku`
     for every SKU actually fetched, *before* it's shipped or decremented —
     a returned SKU that doesn't match what was requested, or a low
     confidence read, is treated as a mispick/mislabeled-shelf shortfall
     rather than shipped on trust. The two tools hand the photo off by a
     short `image_id` (label-api generates and stores the photo, then
     redeems the id itself) rather than by embedding image bytes in either
     tool's MCP result or tool-call arguments — the raw photo never becomes
     part of the OpenAI conversation history, which otherwise ate into this
     agent's token budget fast.
   - [`local-shipping-api`](../local-shipping-api) — ship whatever passed
     verification in one carrier handoff, returning a tracking number.
   - [`supervisor-api`](../supervisor-api) — for a SKU that's short (out of
     stock *or* failed visual verification), first try sourcing it from
     another DC via `request_transfer`; if that comes back unavailable (or
     the SKU is unknown to the WMS at all), escalate to a human via
     `request_help`, without blocking the rest of the order.

   The five servers' MCP tools are connected once (`src/mcp_tools.py`) and
   reused for the worker's lifetime, name-prefixed by service
   (`wms__adjust_inventory`, `robot__fetch_item`, `robot__get_item_photo`,
   `label__infer_sku`, `shipping__ship_order`, `supervisor__request_help`) so
   a single OpenAI tool-calling loop can drive all five. The loop's system
   prompt is built from our own fulfillment policy plus each server's own
   self-declared MCP `instructions` string, so capacity/dock/carrier facts
   stay sourced from the services themselves rather than duplicated here.
   The model finishes by calling a `record_fulfillment_result` tool with a
   structured summary of every line item's outcome. If the loop stalls past
   `MAX_FULFILLMENT_TURNS`, the agent escalates to the supervisor directly
   and returns a degraded result rather than hanging or failing the PO.
6. The result is returned as an A2A artifact: a `DataPart` with the full
   structured JSON (including the `fulfillment` block — per-item status,
   shipment/tracking info, and any escalations) and a `TextPart`
   human-readable summary. If ingestion, extraction, or fulfillment fails
   outright, the task is marked `failed` with an explanatory message
   instead.

## Setup

```bash
cd local-dc-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# for running tests
pip install -r requirements-dev.txt
```

Set `OPENAI_API_KEY` before running the agent for real (extraction and
fulfillment will fail without it; this is not needed to run the test
suite, which mocks the OpenAI, po-ingest-api, and MCP calls). Settings are
defined in [`src/settings.py`](src/settings.py) with reasonable defaults;
copy `.env.example` to `.env` and fill it in to override them, or set
environment variables directly (env vars take precedence over `.env`).

## Run

Fulfillment needs all five other services running too:

```bash
# in separate terminals
cd po-ingest-api && python3 -m src
cd local-wms-api && python3 -m src
cd local-inventory-robot-api && python3 -m src
cd supervisor-api && python3 -m src
cd local-shipping-api && python3 -m src
cd label-api && python3 -m src

# then
export OPENAI_API_KEY=...
cd local-dc-agent && python3 -m src
```

(Or use the root `Makefile`'s `run-*` targets.)

Starts the agent on `http://localhost:9100`. The agent card is served at
`http://localhost:9100/.well-known/agent-card.json`.

### Agentic Safety

`src/guardrails.py`'s heuristic MCP guardrails (the pre-fulfillment
prompt-injection scan, the `wms__adjust_inventory` bound check, tool-result
redaction, and hiding the destructive `reset_*` tools from the model's tool
list — see the dashboard-ui README's "guardrail test POs" section for what
each of these catches) are gated behind a single runtime toggle, on by
default. It's process-local, in-memory state (like everything else in this
service) rather than anything persisted, so it resets to `GUARDRAILS_ENABLED`
on restart:

```bash
curl http://localhost:9100/guardrails                                          # {"enabled": true}
curl -X POST http://localhost:9100/guardrails -d '{"enabled": false}'          # disable for a demo
```

The dashboard UI's "Agentic Safety" switch (top bar) is a thin proxy over
this same endpoint (`GET`/`POST /api/agentic-safety` in `dashboard-ui`), so
flipping it there disables it here too, live, without a restart.

| Env var | Default | Description |
|---|---|---|
| `PO_INGEST_API_URL` | `http://localhost:8000` | Base URL of `po-ingest-api` |
| `WMS_API_URL` | `http://localhost:8001` | Base URL of `local-wms-api` |
| `ROBOT_API_URL` | `http://localhost:8002` | Base URL of `local-inventory-robot-api` |
| `SUPERVISOR_API_URL` | `http://localhost:8003` | Base URL of `supervisor-api` |
| `SHIPPING_API_URL` | `http://localhost:8004` | Base URL of `local-shipping-api` |
| `LABEL_API_URL` | `http://localhost:8005` | Base URL of `label-api` |
| `OPENAI_API_KEY` | *(none)* | Required to run extraction and fulfillment |
| `OPENAI_MODEL` | `gpt-5` | Model used for both extraction and fulfillment |
| `MAX_FULFILLMENT_TURNS` | `20` | Tool-call turns before the fulfillment loop auto-escalates and gives up |
| `GUARDRAILS_ENABLED` | `true` | Startup default for the "Agentic Safety" toggle (see below) |
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `9100` | Bind port |
| `AGENT_URL` | `http://localhost:{PORT}/` | URL advertised in the agent card |
| `MLFLOW_TRACKING_URI` | unset | MLflow tracking server URL. When set, every order gets one MLflow trace covering ingest/extraction/fulfillment, with the extraction and fulfillment OpenAI calls (auto-instrumented via `mlflow.openai.autolog()`) and every outbound MCP tool call nested inside it (see [`src/tracing.py`](src/tracing.py)); left unset, tracing is disabled outright. MLflow's own env vars (`MLFLOW_EXPERIMENT_NAME`, `MLFLOW_WORKSPACE`, `MLFLOW_TRACKING_TOKEN`, `MLFLOW_TRACKING_AUTH`, ...) are read natively by the `mlflow` package alongside this one |

## Usage

Generate a sample PO and send it to the agent's `message/send` JSON-RPC
endpoint as a base64-encoded file part:

```bash
PDF_B64=$(base64 -i ../target/pos/some-po.pdf)
curl -s http://localhost:9100/ \
  -H 'Content-Type: application/json' \
  -d "$(cat <<EOF
{
  "jsonrpc": "2.0", "id": "1", "method": "message/send",
  "params": {
    "message": {
      "role": "user", "messageId": "m1", "kind": "message",
      "parts": [{"kind": "file", "file": {"bytes": "$PDF_B64", "mimeType": "application/pdf", "name": "po.pdf"}}]
    }
  }
}
EOF
)"
```

The response's `result.artifacts[0].parts` contains the structured
`ProcessOrderResult` JSON (`DataPart`, including its `fulfillment` block)
and a human-readable summary (`TextPart`) — the latter includes the
carrier tracking number if the order shipped, or a note about any
escalations raised.

Note `local-wms-api`'s seed inventory only stocks a handful of SKUs (one at
zero on hand) while `test-po-generator`'s product catalog is much larger,
so a generated PO will often exercise both the successful pick-and-ship
path and the supervisor-escalation path in the same run.

## Tests

```bash
pytest
```

Covers order-processing logic (subtotal computation, totals-mismatch
detection, fulfillment summary text), the MCP tool router's
prefixing/routing/error-handling, the fulfillment tool-calling loop
(happy path, escalation, and max-turns fallback), the worker's queue-driven
idle/serial-processing behavior, and the agent executor's event flow —
all against fakes/mocks, no network access, `OPENAI_API_KEY`, or running
services required.
