# Eval Suite

Three EvalHub "bring your own framework" (BYOF) benchmarks for the
distribution-center demo, evaluating it the way [OpenShift AI's EvalHub](https://developers.redhat.com/articles/2026/06/02/evaluation-driven-development-evalhub)
expects a real customer deployment to be evaluated:

| Benchmark | Checks |
|---|---|
| `dc-extraction-accuracy` | Field-level accuracy of `local-dc-agent`'s PO extraction against golden PDFs with known-correct fields, using the exact prompt (MLflow Prompt Registry or local catalog) and tool schema production uses. |
| `dc-mcp-trajectory` | Structural validity of every MCP tool call `local-dc-agent` makes (against the five downstream servers' *live* schemas) plus policy conformance — every stock decrement must be preceded by a `robot__get_item_photo` -> `label__infer_sku` visual-verification pair. |
| `dc-end-to-end` | Whether the agent's final fulfilled quantity and order status match a stock-availability outcome computed in advance from the seed inventory/shelf CSVs. |

Each benchmark works two ways:

1. **Locally, with no EvalHub installed** — a plain `run_local()` function
   per benchmark that this CLI calls directly against whatever services are
   configured. This is the fastest way to check "did my change break
   anything," and what CI/nightly runs can use before EvalHub is deployed.
2. **Registered with a running EvalHub** — the same logic wrapped as an
   `evalhub.adapter.FrameworkAdapter` (`src/adapters/base.py`), combined
   into one weighted collection in `config/evalhub.yaml`
   (`distribution-center-eval-v1`).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The extraction benchmark also needs `OPENAI_API_KEY` (and `PROMPT_SOURCE`,
`MLFLOW_TRACKING_URI` if using the MLflow Prompt Registry) — these are read
from the repo-root `.env` the same way every other service's `make run-*`
target picks them up.

To register benchmarks with a real EvalHub instance instead: `pip install
eval-hub-sdk`.

## Usage

Requires `po-ingest-api` and `local-dc-agent` (plus its five downstream MCP
servers) running — e.g. `make start-all` from the repo root.

```bash
# Run all three benchmarks
python -m src

# Run just one
python -m src --adapter extraction
python -m src --adapter mcp-trajectory
python -m src --adapter end-to-end

# Control the extraction benchmark's generated dataset
python -m src --adapter extraction --n 10 --seed 7
```

Or from the repo root: `make eval-suite ARGS="--adapter extraction"`.

Exits non-zero if any run benchmark scores below its threshold
(`settings.py`'s `*_THRESHOLD` fields, mirrored in `config/evalhub.yaml`'s
`pass_criteria` — keep both in sync by hand, same as
`dashboard-ui/settings.py`'s `DISTRIBUTION_CENTER` mirror).

### Against a real EvalHub

The EvalHub *server* (`trustyai.opendatahub.io/v1alpha1 EvalHub`, owned by
the TrustyAI operator) is cluster infrastructure someone else stands up once
per OpenShift AI namespace — `eval-suite` doesn't declare or apply that CRD,
and there is no per-job Kubernetes resource you write by hand either.
Registering a provider/collection and submitting a run are REST/CLI calls
against that already-running server.

1. **Build and push the image.** `deploy/Jenkinsfile`'s "Create Docker Image
   for eval-suite" stage does this on every build, pushing
   `registry.home.glroland.com/distribution-center/eval-suite:$BUILD_NUMBER`
   (no `:latest` tag is pushed for this image, unlike `vision-ml-trainer`).

2. **Point `config/evalhub.yaml` at that build.** Edit
   `provider.runtime.k8s.image`, replacing the `REPLACE_WITH_BUILD_NUMBER`
   placeholder with the Jenkins build number you want registered.

3. **Register the provider and collection.** Requires the `evalhub` CLI
   (`pip install eval-hub-sdk`) already authenticated against your target
   EvalHub server:

   ```bash
   evalhub providers create   --file config/evalhub.yaml
   evalhub collections create --file config/evalhub.yaml
   ```

   Or from the repo root: `make register-eval-suite` (runs the same two
   commands against `eval-suite/config/evalhub.yaml`).

4. **Run it.** Either from the OpenShift AI dashboard's Evaluation Stack UI
   (Tech Preview — the registered collection should appear there once step 3
   completes) or from the CLI/CI:

   ```bash
   evalhub eval run --collection distribution-center-eval-v1 --model-url $AGENT_URL --wait
   ```

Re-registering after a new image build means repeating steps 2-3 — there's
no `:latest`-follows-main auto-update; the collection always points at
whatever build number was last registered.

## Layout

- `src/seed_data.py` — reads the same CSVs `local-wms-api`/
  `local-inventory-robot-api` seed their in-memory stores from, to compute
  expected stock outcomes without duplicating either service's logic.
- `src/pdf_builder.py` — minimal, self-contained PO PDF renderer (kept
  independent of `test-po-generator`'s templates — see its own docstring
  for why).
- `src/dataset.py` — builds golden extraction cases and known-outcome
  fulfillment scenarios from the above two.
- `src/agent_client.py` / `src/ingest_client.py` — thin clients mirroring
  `dashboard-ui`'s and `local-dc-agent`'s own, so benchmarks call the real
  services the same way production callers do.
- `src/webhook_receiver.py` — local HTTP receiver for `local-dc-agent`'s
  `progress_webhook` tool-call events.
- `src/mcp_schema_client.py` — fetches live MCP tool schemas from all five
  downstream servers for the trajectory benchmark.
- `src/prompts.py` — minimal mirror of `local-dc-agent/src/prompts.py`'s
  prompt-loading contract (no caching/trace-tagging — this runs as a
  one-shot CLI, not a server).
- `src/scoring.py` — all scoring logic, kept separate from the adapters so
  it's testable without live services or a model.
- `src/adapters/` — the three benchmarks; `base.py` bridges a benchmark's
  `run_local()` result into the real EvalHub `FrameworkAdapter` contract.
- `src/evalhub_entrypoint.py` — the process EvalHub actually invokes,
  dispatching to the right adapter by `JobSpec.benchmark_id`.
- `config/evalhub.yaml` — provider + collection + job definitions.
