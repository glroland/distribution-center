# Prompt Registry

Every service that owns an LLM/MCP-instruction prompt keeps its own
`prompts.json` (a sibling of its `src/`) -- one entry per prompt, with a
stable id, its source location, and its template text. This is deliberately
*not* one central catalog: each `prompts.json` is baked into its own
service's container image (see that service's `Containerfile`), so
`PROMPT_SOURCE=local` works standalone in a deployed pod without MLflow, and
each image only ever needs files from its own build context -- no reaching
into a sibling directory, no ConfigMap tricks.

This directory (`prompt-registry/`) holds only the CLI, `src/cli.py`, which
discovers every `<service>/prompts.json` in the repo and registers each
prompt as a version in the MLflow Prompt Registry
(`mlflow.genai.register_prompt`).

Every service's own `src/prompts.py` (`get_prompt(prompt_id)`, cached per
process) reads its prompts back at runtime -- from MLflow when
`PROMPT_SOURCE=mlflow` (the deployed default; see
`deploy/helm/templates/_helpers.tpl`'s `adc.mlflow.envVars`), or from that
service's own `prompts.json` directly when `PROMPT_SOURCE=local` (the
default for local dev/tests, so nothing needs a live MLflow server). Both
modes render the `{{variable}}` templates the same way (via mlflow's own
`PromptVersion.format()`), so switching `PROMPT_SOURCE` never changes
rendered content -- only where it was fetched from. Each service also tags
its own MLflow traces with `prompt.<id>: <version>` (or `local`) for every
prompt it loads, so a trace shows exactly which prompt version produced it.

## What's cataloged

Prompt ids are namespaced by the owning service (`<service>.<file>.<symbol>`)
-- MLflow's Prompt Registry has no folder/namespace concept of its own, so
this prefix is what groups a service's prompts together alphabetically in
the registry UI. Every registered version is also tagged `service`,
`source_file`, `source_symbol`, `role`, and `variables` for filtering.

| id | catalog file | source |
|---|---|---|
| `dc-agent.order_extraction.system_prompt` | `local-dc-agent/prompts.json` | `local-dc-agent/src/order_extraction.py` |
| `dc-agent.fulfillment.policy_prompt` | `local-dc-agent/prompts.json` | `local-dc-agent/src/fulfillment.py` |
| `dc-agent.fulfillment.continue_nudge` | `local-dc-agent/prompts.json` | `local-dc-agent/src/fulfillment.py` |
| `dc-agent.fulfillment.escalation_timeout_question` | `local-dc-agent/prompts.json` | `local-dc-agent/src/fulfillment.py` |
| `local-wms-api.mcp_server.instructions` | `local-wms-api/prompts.json` | `local-wms-api/src/mcp_server.py` |
| `local-inventory-robot-api.mcp_server.instructions` | `local-inventory-robot-api/prompts.json` | `local-inventory-robot-api/src/mcp_server.py` |
| `supervisor-api.mcp_server.instructions` | `supervisor-api/prompts.json` | `supervisor-api/src/mcp_server.py` |
| `local-shipping-api.mcp_server.instructions` | `local-shipping-api/prompts.json` | `local-shipping-api/src/mcp_server.py` |
| `label-api.mcp_server.instructions` | `label-api/prompts.json` | `label-api/src/mcp_server.py` |

The five `*.mcp_server.instructions` entries aren't sent to an LLM by their
own service -- they're each MCP server's `instructions=` string, which
`local-dc-agent` reads over MCP at connect time and appends to the
fulfillment agent's system prompt (see `fulfillment._build_system_prompt`).
They're cataloged in their owning service anyway because they're effectively
that service's own prompt fragment. Because that render happens at server
startup rather than per-request, there's no active MLflow trace to tag at
that point -- each of those 5 services just logs which prompt id and version
it loaded at startup instead.

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
`Makefile` already exports them for `make` targets (`make load-prompts`
wraps this CLI).

```bash
# Discover every <service>/prompts.json and register all of them
python -m src

# See what would be registered without contacting MLflow
python -m src --dry-run

# Register a single prompt (still discovers across every catalog file first)
python -m src --only dc-agent.fulfillment.policy_prompt

# Register from specific catalog file(s) instead of auto-discovering
python -m src --file ../local-dc-agent/prompts.json --file ../label-api/prompts.json

# Point at a different MLflow server
python -m src --tracking-uri https://my-mlflow-server/
```

Each run creates a new prompt version (or a new prompt if the name doesn't
exist yet) tagged with `service`, `source_file`, `source_symbol`, `role`, and
`variables`. Re-running after editing a service's `prompts.json` registers a
new version rather than overwriting the old one, so history is preserved in
MLflow. Registration fails loudly if the same prompt id appears in more than
one catalog file -- ids are meant to be unique across the whole repo.
