# Dashboard UI

A single-page control room for running and watching the distribution center
demo: pick a purchase order, send it, and watch it move through ingest, LLM
extraction, inventory checks, robot picking, shipping, and any
human-in-the-loop escalation - live, in the browser.

This service is a thin backend-for-frontend. It doesn't own any business
state itself; it orchestrates calls to the other services in this repo and
serves the static dashboard UI.

## How live updates work

The dc-agent's `message/send` call is a single blocking A2A request with no
built-in streaming (its agent card declares `capabilities.streaming=False`).
To watch a PO move step by step anyway, this dashboard passes a
`progress_webhook` URL in the outbound message's `metadata`
(`local-dc-agent/src/agent_executor.py:_build_progress_hook`). If present,
the agent POSTs an event to it after ingest, after LLM extraction, after
totals processing, after *every* MCP tool call in the fulfillment loop (with
the tool's name, arguments, and raw result), and once more with the final
`FulfillmentResult`. This dashboard fans those events out to the browser over
Server-Sent Events (`GET /api/runs/{run_id}/stream`) and derives all the
"what's happening right now" UI (inventory deltas, robot position, shipments,
escalations) by parsing each tool call's result according to which service it
belongs to - no polling required during an active run.

If the agent can't reach the webhook (dashboard not running, network issue),
processing is entirely unaffected - the hook is best-effort and swallows its
own errors.

Between runs, and for the initial page load, the dashboard falls back to
plain polling of each service's REST API (inventory, robot status/shelf
grid, shipments, open help requests).

## Run

Needs `po-ingest-api`, `local-wms-api`, `local-inventory-robot-api`,
`supervisor-api`, `local-shipping-api`, and `local-dc-agent` all running
first (see the root README/Makefile). A handful of sample PO PDFs are
checked into `data/pos/` and packaged into this service's own container
image (`COPY data ./data` in the Containerfile), so there's always
something to pick from out of the box - including in Kubernetes, where
`test-po-generator/output/` and `target/pos/` don't exist. For local dev,
you can still add more by dropping pre-generated ones into
`test-po-generator/output/`, or generating fresh ones via `make
generate-pos` (written to `target/pos/`); both are searched in addition to
the packaged set.

`data/guardrail-test-pos/` is packaged the same way (same `COPY data
./data`, since it's a subdirectory of `data/`) and always shows up in the
same PO picker alongside the legitimate samples. These are adversarial POs,
each crafted to trip one of `local-dc-agent`'s MCP guardrails (prompt
injection via SHIP TO/line-item text, an out-of-bounds inventory decrement,
a disallowed destructive tool call, hidden white-on-white text, prompt
exfiltration, a combined attack) - run one through the pipeline to confirm
the corresponding guardrail actually fires instead of just trusting that it
does. See `data/guardrail-test-pos/README.md` for what each file tests and
the expected outcome.

The **Agentic Safety** switch in the top bar toggles those same guardrails
live, on `local-dc-agent`, via `GET`/`POST /api/agentic-safety` here (a thin
proxy to that service's own `GET`/`POST /guardrails` - see its README). On
by default; flip it off to run one of the `guardrail-test-pos/` PDFs
unprotected for comparison, e.g. to show the ship-to hijack actually
reaching the shipping tool instead of being escalated.

```bash
cd dashboard-ui
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src
```

Starts on `http://localhost:8090` - open that in a browser.

| Env var | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `8090` | Bind port |
| `PUBLIC_URL` | `http://localhost:8090` | Base URL other services use to reach this one (the dc-agent's progress webhook target) |
| `SUPERVISOR_API_URL` | `http://localhost:8003` | Base URL of `supervisor-api` |
| `PO_INGEST_API_URL` | `http://localhost:8000` | Base URL of `po-ingest-api` (currently unused directly, reserved) |
| `LABEL_API_URL` | `http://localhost:8005` | Base URL of `label-api`, proxied by `GET /api/stickers/{sku}` for the sticker-photo preview |
| `PO_DIRS` | `target/pos,test-po-generator/output` | Comma-separated *additional* directories (relative to the repo root) to look for demo PO PDFs, on top of the packaged `data/pos/` and `data/guardrail-test-pos/` (always searched, not configurable) |

## Distribution center

`src/settings.py`'s `DISTRIBUTION_CENTER` is the dashboard's registry of the
one DC it talks to - name, display name, and the agent/WMS/robot/shipping
URLs for its stack, mirroring `deploy/helm/values.yaml`'s
`distributionCenter` block.

## API

All under `/api`:

| Method | Path | Description |
|---|---|---|
| `GET` | `/dc` | Distribution center info (name, URLs, live location) |
| `GET` | `/pos` | List available PO PDFs |
| `GET` | `/pos/{filename}/file` | Raw PDF bytes, for browser preview |
| `GET` | `/map` | Full shelf grid scan + live robot status |
| `GET` | `/inventory` | WMS ledger passthrough |
| `GET` | `/shipments?po_number=` | Shipments passthrough |
| `POST` | `/reset` | Reset inventory, robot, and shipments |
| `GET` | `/help-requests?status=` | Supervisor help requests passthrough |
| `GET` | `/stickers/{sku}?color_mode=&image_format=` | Sticker photo passthrough to `label-api`, for the "captured sticker photo" preview on each robot pick |
| `POST` | `/help-requests/{id}/resolve` | Resolve a help request |
| `POST` | `/runs` | Body `{"filename": str}`; starts sending a PO, returns `{"run_id"}` |
| `GET` | `/runs/{run_id}/stream` | SSE stream of that run's events |
| `POST` | `/internal/events/{run_id}` | Webhook target the dc-agent posts progress events to |

Note resolving a help request doesn't retroactively resume the PO it was
raised for - per the dc-agent's fulfillment policy, an escalation is
fire-and-forget so one bad line item never blocks the rest of the order. The
"7 · Human-in-the-Loop" panel is accurate to that: it lets a human answer the
agent's question, but that PO's run has already moved on.
