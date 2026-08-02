# Prompt Registry

`prompts.json` is a catalog of every LLM prompt in the distribution-center
demo -- one entry per prompt, with a stable id, its source location, and its
template text. `src/cli.py` is a CLI that registers each one as a version in
the MLflow Prompt Registry (`mlflow.genai.register_prompt`).

This is a one-way export tool only: it reads `prompts.json` and writes to
MLflow. No application service reads prompts back from MLflow at runtime yet
-- every service still uses its own hardcoded prompt constant. The registry
exists so prompts can be reviewed/versioned/diffed in MLflow ahead of a
future change to load them dynamically.

## What's cataloged

| id | source |
|---|---|
| `dc-agent.order_extraction.system_prompt` | `local-dc-agent/src/order_extraction.py` |
| `dc-agent.fulfillment.policy_prompt` | `local-dc-agent/src/fulfillment.py` |
| `dc-agent.fulfillment.continue_nudge` | `local-dc-agent/src/fulfillment.py` |
| `dc-agent.fulfillment.escalation_timeout_question` | `local-dc-agent/src/fulfillment.py` |
| `local-wms-api.mcp_server.instructions` | `local-wms-api/src/mcp_server.py` |
| `local-inventory-robot-api.mcp_server.instructions` | `local-inventory-robot-api/src/mcp_server.py` |
| `supervisor-api.mcp_server.instructions` | `supervisor-api/src/mcp_server.py` |
| `local-shipping-api.mcp_server.instructions` | `local-shipping-api/src/mcp_server.py` |

The four `*.mcp_server.instructions` entries aren't sent to an LLM by their
own service -- they're each MCP server's `instructions=` string, which
`local-dc-agent` reads over MCP at connect time and appends to the
fulfillment agent's system prompt (see `fulfillment._build_system_prompt`).
They're cataloged here because they're effectively prompt fragments, even
though they live in other services' codebases.

Templates use MLflow's `{{variable}}` syntax for the handful of prompts that
are built from runtime values (e.g. the robot instructions embed the grid
size and dock location from `GRID_WIDTH`/`GRID_HEIGHT`/`DOCK_X`/`DOCK_Y`/
`CARRY_CAPACITY`). `default_variables` on those entries gives the values that
match `.env.example`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Requires `MLFLOW_TRACKING_URI` (and `MLFLOW_WORKSPACE`/`MLFLOW_TRACKING_AUTH`
if your server needs them) in the environment -- these are the same env vars
every other service in this repo reads natively via `mlflow`, and the root
`Makefile` already exports them for `make` targets.

```bash
# Register every prompt in prompts.json
python -m src

# See what would be registered without contacting MLflow
python -m src --dry-run

# Register a single prompt
python -m src --only dc-agent.fulfillment.policy_prompt

# Point at a different catalog file or MLflow server
python -m src --file other-prompts.json --tracking-uri https://my-mlflow-server/
```

Each run creates a new prompt version (or a new prompt if the name doesn't
exist yet) tagged with `service`, `source_file`, `source_symbol`, `role`, and
`variables`. Re-running after editing `prompts.json` registers a new version
rather than overwriting the old one, so history is preserved in MLflow.
