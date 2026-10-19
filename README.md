# Agentic Distribution Center Demo

A demo of an AI-driven distribution center. An A2A agent ingests a purchase
order PDF, extracts it with an LLM, checks and fulfills inventory through a
virtual picking robot, verifies each pick with on-device vision, ships
whatever was retrieved, and escalates to a human supervisor when anything's
short or unknown — all watchable live in a browser dashboard.

The system is eight independent Python services (each with its own venv,
requirements, and Containerfile) wired together over HTTP and MCP, plus a
PDF generator for demo data, a vision training project, and an EvalHub
benchmark suite. Everything runs locally via the root `Makefile`, and
deploys to OpenShift as a Helm chart.

## What the agent does

Given a PO PDF, the `local-dc-agent` pipeline:

1. **Ingest** — converts the PDF to Markdown/JSON via `po-ingest-api`
   (Docling, no LLM).
2. **Extract** — a forced-tool-call LLM step pulls structured fields
   (PO number, vendor, buyer, ship-to, payment terms, line items) out of
   arbitrary letterhead layouts.
3. **Process** — recomputes the subtotal and flags any stated-total
   mismatch.
4. **Fulfill** — an LLM tool-calling loop drives five MCP servers:
   - `local-wms-api` — check on-hand stock, decrement after verified pick.
   - `local-inventory-robot-api` — `plan_and_fetch_items` sends a single
     picking robot across a 2D shelf grid (capacity-driven dock
     round-trips included); `get_item_photo` captures the shelf sticker.
   - `label-api` — `infer_sku` runs the `vision-ml` three-stage pipeline
     (localize → orient → OCR) in-process against the sticker photo. A SKU
     that doesn't match what was requested, or a low-confidence read, is
     treated as a mispick shortfall — nothing ships on trust.
   - `local-shipping-api` — mock carrier handoff returning a tracking
     number.
   - `supervisor-api` — for shortfalls: try an inter-DC transfer first
     (`request_transfer`), then escalate to a human (`request_help`),
     without blocking the rest of the order.

The loop ends with a structured per-item result returned as an A2A
artifact. Each service's own MCP `instructions` string is injected into the
agent's system prompt at runtime, so domain rules (capacity limits, dock
behavior, carriers) stay sourced from the services themselves.

## Services

| Service | Port | Role |
|---|---|---|
| `po-ingest-api` | 8000 | PDF → Markdown/JSON via Docling |
| `local-dc-agent` | 9100 | The A2A agent orchestrating extraction + fulfillment |
| `local-wms-api` | 8001 | In-memory inventory ledger for one virtual DC |
| `local-inventory-robot-api` | 8002 | Simulated picking robot on a 2D shelf grid |
| `supervisor-api` | 8003 | Human-in-the-loop escalation + inter-DC transfer queue |
| `local-shipping-api` | 8004 | Mock carrier handoff (tracking numbers) |
| `label-api` | 8005 | Synthetic sticker photos + local SKU inference (vision-ml models) |
| `dashboard-ui` | 8090 | Static "control room" UI that drives and watches a run |
| `test-po-generator` | — | CLI: generates sample PO PDFs |
| `eval-suite` | — | CLI: EvalHub BYOF benchmarks (extraction, trajectory, end-to-end) |
| `vision-ml` | — | Notebooks training the 3-stage sticker-OCR pipeline |
| `prompt-registry` | — | CLI: registers each service's `prompts.json` into the MLflow Prompt Registry |

Each service has its own `README.md` documenting endpoints, MCP tools,
env vars, and tests — read that before working in one.

## Getting started

Prerequisites: a recent Python 3 (3.13/3.14 per the service venvs), `make`,
and (for the agent to actually call the LLM) an OpenAI-compatible
endpoint. The repo-root `.env` is the single
source of env config for all `make` targets — copy and edit it:

```bash
cp .env.example .env
# set OPENAI_BASE_URL / OPENAI_MODEL / OPENAI_API_KEY to whatever you use
```

`.env.example` ships with safe placeholders (a `localhost:9999` base URL
and `nokeyneeded`) so the services can all start and be exercised without a
real model.

```bash
make install        # pip/uv install every requirements.txt in the repo
make generate-pos ARGS="--count 25"   # sample PO PDFs into target/pos/
make start-all      # start all 8 services in the background
make status-all     # confirm they're up
```

Then open the dashboard at **http://localhost:8090**: pick a PO, send it,
and watch it move through ingest → extraction → fulfillment → shipping in
real time. The dashboard streams the agent's `progress_webhook` events over
SSE (inventory deltas, robot position, tool calls, escalations); between
runs it polls each service's REST API.

Handy day-to-day targets (`make help` for the full list):

```bash
tail -f target/logs/*.log   # service logs
make restart-all             # kill-all + start-all + tail logs
make kill-all                # stop everything start-all started
make run-local-dc-agent      # run a single service in the foreground
make test                    # unit tests for every service that has them
```

There's no repo-wide typecheck/lint config; per-service tests are plain
`pytest` in each service directory (or `cd <service> && pytest`).

## Evaluating

`eval-suite` ships three benchmarks for the agent, each runnable two ways:
locally with no EvalHub installed (`make eval-suite ARGS="--adapter all"`,
services must be running), or as a registered weighted collection
(`distribution-center-eval-v1`) against a live OpenShift AI EvalHub
instance (`make register-eval-suite`, then `make evalhub-run ARGS="--wait"`):

| Benchmark | Checks |
|---|---|
| `dc-extraction-accuracy` | Field accuracy of PO extraction against golden PDFs with known-correct fields |
| `dc-mcp-trajectory` | Every MCP call validates against the five servers' *live* schemas, and every stock decrement is preceded by a `get_item_photo` → `infer_sku` verification pair |
| `dc-end-to-end` | Final fulfilled quantities/order status match outcomes precomputed from the seed CSVs (rules-based — the ground truth is computable, not a judgment call) |

## Prompts

Every LLM-facing service keeps its prompts in its own `prompts.json`. At
runtime they load from the MLflow Prompt Registry (`PROMPT_SOURCE=mlflow`)
or straight from the local file (`PROMPT_SOURCE=local`, the default) —
rendered identically either way, and each trace is tagged with the prompt
id + version used. Push updates with `make load-prompts` (add
`ARGS="--dry-run"` to preview).

## Deployment

`deploy/helm` is an umbrella Helm chart (one subchart per component:
`poIngestApi`, `supervisorApi`, `labelApi`, `dashboardUi` as cluster
singletons; `dcAgent`, `wmsApi`, `robotApi`, `shippingApi` as the single
DC's components), plus `deploy/Jenkinsfile` for image builds. Deployed
services default to `PROMPT_SOURCE=mlflow` and talk to each other over
in-cluster Service DNS; inventory/shelf seed data is mounted from
ConfigMaps.

## Demo data

- `products.csv` (repo root) — the catalog `test-po-generator` samples line
  items from.
- `local-wms-api/data/inventory.csv` + `local-inventory-robot-api/data/shelves.csv`
  — on-hand and shelf stock. The overlap with the product catalog is
  *intentionally partial* (a few SKUs are stocked, one at zero), so a
  random PO exercises both a successful pick-and-ship and a supervisor
  escalation in the same run.
- `dashboard-ui/data/guardrail-test-pos/` — adversarial POs (prompt
  injection, out-of-bounds decrements, exfiltration attempts) for testing
  the dc-agent's agentic-safety guardrails, toggleable live in the
  dashboard.
- `vision-ml` — trains the localize → orient → OCR CNNs whose checkpoints
  `label-api` ships and runs in-process; its bulk sticker endpoint's
  optional manifest is the ground-truth source for training.
