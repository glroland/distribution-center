# Dashboard UI

A single-page control room for running and watching the distribution center
demo: pick a purchase order and a distribution center, send it, and watch it
move through ingest, LLM extraction, inventory checks, robot picking,
shipping, and any human-in-the-loop escalation - live, in the browser.

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
first (see the root README/Makefile), plus at least one PO PDF on disk -
either pre-generated ones already in `test-po-generator/output/`, or fresh
ones via `make generate-pos` (written to `target/pos/`).

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
| `PO_DIRS` | `target/pos,test-po-generator/output` | Comma-separated directories (relative to the repo root) to look for demo PO PDFs |

## Distribution centers

`src/settings.py`'s `DISTRIBUTION_CENTERS` list is the dashboard's registry of
selectable DCs - name, display name, and the agent/WMS/robot/shipping URLs
for that DC's stack, mirroring `deploy/helm/values.yaml`'s
`distributionCenters` list. Only one DC (`distribution-center-a`, matching
the default local ports) is defined out of the box; add another entry to
make a second one selectable in the UI once you're running a second stack on
different ports.

## API

All under `/api`:

| Method | Path | Description |
|---|---|---|
| `GET` | `/dcs` | List configured distribution centers |
| `GET` | `/dcs/{name}/pos` | List available PO PDFs |
| `GET` | `/pos/{filename}/file` | Raw PDF bytes, for browser preview |
| `GET` | `/dcs/{name}/map` | Full shelf grid scan + live robot status |
| `GET` | `/dcs/{name}/inventory` | WMS ledger passthrough |
| `GET` | `/dcs/{name}/shipments?po_number=` | Shipments passthrough |
| `POST` | `/dcs/{name}/reset` | Reset inventory, robot, and shipments for a DC |
| `GET` | `/help-requests?status=` | Supervisor help requests passthrough |
| `POST` | `/help-requests/{id}/resolve` | Resolve a help request |
| `POST` | `/runs` | Body `{"dc": str, "filename": str}`; starts sending a PO, returns `{"run_id"}` |
| `GET` | `/runs/{run_id}/stream` | SSE stream of that run's events |
| `POST` | `/internal/events/{run_id}` | Webhook target the dc-agent posts progress events to |

Note resolving a help request doesn't retroactively resume the PO it was
raised for - per the dc-agent's fulfillment policy, an escalation is
fire-and-forget so one bad line item never blocks the rest of the order. The
"7 · Human-in-the-Loop" panel is accurate to that: it lets a human answer the
agent's question, but that PO's run has already moved on.
