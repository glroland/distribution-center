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
                             local-shipping-api + supervisor-api (via MCP)
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
   order, using MCP tools exposed by four other services in this repo:
   - [`local-wms-api`](../local-wms-api) — check on-hand quantity for each
     SKU before doing anything physical, and decrement it once stock has
     actually been picked.
   - [`local-inventory-robot-api`](../local-inventory-robot-api) — locate
     each SKU's shelf, drive the robot there, pick it up, and deliver
     everything to the dock. What `deliver_items` reports as actually
     delivered — not the requested quantity — is what gets shipped and
     what decrements the WMS ledger. Stock arriving via an approved
     inter-DC transfer is placed on a shelf with `restock_shelf` before
     being picked and delivered the normal way.
   - [`local-shipping-api`](../local-shipping-api) — ship whatever was
     delivered in one carrier handoff, returning a tracking number.
   - [`supervisor-api`](../supervisor-api) — for a SKU that's short, first
     try sourcing the shortfall from another DC via `request_transfer`; if
     that comes back unavailable (or the SKU is unknown to the WMS at all),
     escalate to a human via `request_help`, without blocking the rest of
     the order.

   The four servers' MCP tools are connected once (`src/mcp_tools.py`) and
   reused for the worker's lifetime, name-prefixed by service
   (`wms__adjust_inventory`, `robot__fetch_item`, `shipping__ship_order`,
   `supervisor__request_help`) so a single OpenAI tool-calling loop can
   drive all four. The loop's system prompt is built from our own
   fulfillment policy plus each server's own self-declared MCP
   `instructions` string, so capacity/dock/carrier facts stay sourced from
   the services themselves rather than duplicated here. The model finishes
   by calling a `record_fulfillment_result` tool with a structured summary
   of every line item's outcome. If the loop stalls past
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

Fulfillment needs all four other services running too:

```bash
# in separate terminals
cd po-ingest-api && python3 -m src
cd local-wms-api && python3 -m src
cd local-inventory-robot-api && python3 -m src
cd supervisor-api && python3 -m src
cd local-shipping-api && python3 -m src

# then
export OPENAI_API_KEY=...
cd local-dc-agent && python3 -m src
```

(Or use the root `Makefile`'s `run-*` targets.)

Starts the agent on `http://localhost:9100`. The agent card is served at
`http://localhost:9100/.well-known/agent-card.json`.

| Env var | Default | Description |
|---|---|---|
| `PO_INGEST_API_URL` | `http://localhost:8000` | Base URL of `po-ingest-api` |
| `WMS_API_URL` | `http://localhost:8001` | Base URL of `local-wms-api` |
| `ROBOT_API_URL` | `http://localhost:8002` | Base URL of `local-inventory-robot-api` |
| `SUPERVISOR_API_URL` | `http://localhost:8003` | Base URL of `supervisor-api` |
| `SHIPPING_API_URL` | `http://localhost:8004` | Base URL of `local-shipping-api` |
| `OPENAI_API_KEY` | *(none)* | Required to run extraction and fulfillment |
| `OPENAI_MODEL` | `gpt-5` | Model used for both extraction and fulfillment |
| `MAX_FULFILLMENT_TURNS` | `1000` | Tool-call turns before the fulfillment loop auto-escalates and gives up |
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `9100` | Bind port |
| `AGENT_URL` | `http://localhost:{PORT}/` | URL advertised in the agent card |

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
