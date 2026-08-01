# Local Inventory Robot API

A small demo of a single warehouse robot moving on a 2D grid of shelves to
fetch inventory. Shelf stock is seeded from a CSV file and held entirely in
memory - the robot's position, what it's carrying, and shelf stock levels are
all lost on restart, or can be discarded at any time via the reset
endpoint/tool.

The robot can move to any grid cell, pick stock off the shelf at its current
cell into its own carry basket (up to a capacity limit), and drop everything
it's carrying once it's back at the dock.

## Setup

```bash
cd local-inventory-robot-api
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

Starts the API on `http://0.0.0.0:8002`. Interactive docs are available at
`http://localhost:8002/docs`.

Settings are defined in [`src/settings.py`](src/settings.py) with reasonable
defaults; copy `.env.example` to `.env` and fill it in to override them, or
set environment variables directly (env vars take precedence over `.env`).

| Env var | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `8002` | Bind port |
| `SHELVES_CSV_PATH` | `data/shelves.csv` | Path to the seed CSV (relative paths resolve against the project root) |
| `GRID_WIDTH` | `10` | Grid width; valid x coordinates are `0..GRID_WIDTH-1` |
| `GRID_HEIGHT` | `10` | Grid height; valid y coordinates are `0..GRID_HEIGHT-1` |
| `DOCK_X` | `0` | Dock x coordinate; the robot starts here and can only deliver here |
| `DOCK_Y` | `0` | Dock y coordinate |
| `CARRY_CAPACITY` | `100` | Max total units the robot can carry across all SKUs at once |

## Shelf data

[`data/shelves.csv`](data/shelves.csv) has columns `sku`, `qty`, `location_x`,
`location_y`. It's loaded into memory on startup and is the source of truth
for `POST /reset`. A SKU may appear on multiple rows to stock it at more than
one shelf location.

## REST API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/location` | Robot's current `{x, y}` |
| `GET` | `/status` | Robot's location, carried items, and capacity |
| `POST` | `/move` | Body `{"x": int, "y": int}`; move the robot (400 if outside the grid) |
| `GET` | `/shelf?x=&y=` | Stock at `(x, y)`, or the robot's current location if omitted |
| `GET` | `/find/{sku}` | Every shelf location stocking `sku`, with on-hand quantity |
| `POST` | `/pick` | Body `{"sku": str, "qty": int}` (`qty > 0`); pick stock off the shelf at the robot's current location (404 unknown SKU there, 400 insufficient stock or over capacity) |
| `POST` | `/deliver` | Drop everything the robot is carrying (400 unless at the dock) |
| `POST` | `/reset` | Reload shelf stock from the seed CSV and return the robot to the dock, empty-handed |

```bash
curl -X POST http://localhost:8002/move -H 'Content-Type: application/json' -d '{"x": 3, "y": 5}'
curl -X POST http://localhost:8002/pick -H 'Content-Type: application/json' -d '{"sku": "SKU-1001", "qty": 10}'
```

## MCP

The service also exposes an MCP server (Streamable HTTP transport) at
`http://localhost:8002/mcp`, in the same process as the REST API, sharing the
same in-memory robot state. Tools are coarse-grained for LLM use rather than
one-to-one with the REST endpoints, and are meant to be chained together to
fetch inventory:

| Tool | Args | Description |
|---|---|---|
| `get_robot_status` | - | Current location, carried items, and capacity |
| `find_item` | `sku: str` | Shelf locations stocking `sku`, with on-hand quantity at each |
| `get_shelf_inventory` | `x: int \| None`, `y: int \| None` | Everything stocked at `(x, y)`, or the robot's current location if omitted |
| `move_robot` | `x: int`, `y: int` | Move the robot to `(x, y)` |
| `fetch_item` | `sku: str`, `qty: int` | Pick `qty` of `sku` off the shelf at the robot's current location |
| `deliver_items` | - | Drop everything carried, at the dock only |
| `reset_robot` | - | Reload shelf stock and return the robot to the dock, empty-handed |

A typical agent workflow: `find_item` to locate a SKU, `move_robot` to that
shelf, `fetch_item` to pick it up, `move_robot` back to the dock, then
`deliver_items`.

Connect with any MCP client that supports Streamable HTTP, e.g. the `mcp`
Python SDK's `mcp.client.streamable_http.streamable_http_client`.

## Tests

```bash
pytest
```

Covers the in-memory robot model, the REST API, and the MCP tools.
