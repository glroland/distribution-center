# Local WMS API

A small demo Warehouse Management System for a single virtual location.
Inventory (SKU, on-hand quantity, and bin coordinates) is seeded from a CSV
file and held entirely in memory - changes made through the API are lost on
restart, or can be discarded at any time via the reset endpoint/tool.

## Setup

```bash
cd local-wms-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# for running tests
pip install -r requirements-dev.txt
```

## Run

```bash
python -m src
```

Starts the API on `http://0.0.0.0:8001`. Interactive docs are available at
`http://localhost:8001/docs`.

Settings are defined in [`src/settings.py`](src/settings.py) with reasonable
defaults; copy `.env.example` to `.env` and fill it in to override them, or
set environment variables directly (env vars take precedence over `.env`).

| Env var | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `8001` | Bind port |
| `LOCATION_NAME` | `DC-VIRTUAL-01` | Name of the virtual location this instance manages |
| `INVENTORY_CSV_PATH` | `data/inventory.csv` | Path to the seed CSV (relative paths resolve against the project root) |
| `MLFLOW_TRACKING_URI` | unset | MLflow tracking server URL. When set, every MCP tool call is traced as an MLflow span (see [`src/tracing.py`](src/tracing.py)); left unset, tracing is disabled outright. MLflow's own env vars (`MLFLOW_EXPERIMENT_NAME`, `MLFLOW_WORKSPACE`, `MLFLOW_TRACKING_TOKEN`, `MLFLOW_TRACKING_AUTH`, ...) are read natively by the `mlflow` package alongside this one |

## Inventory data

[`data/inventory.csv`](data/inventory.csv) has columns `sku`, `on_hand_qty`,
`location_x`, `location_y`. It's loaded into memory on startup and is the
source of truth for `POST /inventory/reset`.

## REST API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/location` | Get the virtual location's name |
| `GET` | `/inventory` | List every SKU |
| `GET` | `/inventory/{sku}` | Get on-hand quantity and bin location for a SKU (404 if unknown) |
| `POST` | `/inventory/{sku}/increment` | Body `{"qty": int}` (`qty > 0`); receive stock for a SKU |
| `POST` | `/inventory/{sku}/decrement` | Body `{"qty": int}` (`qty > 0`); ship stock for a SKU (400 if it would go below zero) |
| `POST` | `/inventory/reset` | Reload inventory from the seed CSV, discarding all changes |

```bash
curl -X POST http://localhost:8001/inventory/SKU-1001/decrement \
  -H 'Content-Type: application/json' -d '{"qty": 5}'
```

## MCP

The service also exposes an MCP server (Streamable HTTP transport) at
`http://localhost:8001/mcp`, in the same process as the REST API, sharing the
same in-memory inventory store. Tools are coarse-grained for LLM use rather
than one-to-one with the REST endpoints:

| Tool | Args | Description |
|---|---|---|
| `get_location` | - | Name of the virtual location |
| `get_inventory_status` | `sku: str \| None` | On-hand quantity and bin location for one SKU, or every SKU if omitted |
| `adjust_inventory` | `sku: str`, `delta: int` | Receive (`delta > 0`) or ship (`delta < 0`) stock for a SKU |
| `reset_inventory` | - | Reload inventory from the seed CSV |

Connect with any MCP client that supports Streamable HTTP, e.g.
`fastmcp.Client`.

## Tests

```bash
pytest
```

Covers the in-memory store, the REST API, and the MCP tools.
