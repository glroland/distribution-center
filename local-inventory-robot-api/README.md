# Local Inventory Robot API

A small demo of a single warehouse robot moving on a 2D grid of shelves to
fetch inventory. Shelf stock is seeded from a CSV file and held entirely in
memory - the robot's position, what it's carrying, and shelf stock levels are
all lost on restart, or can be discarded at any time via the reset
endpoint/tool.

The robot can move to any grid cell, pick stock off the shelf at its current
cell into its own carry basket (up to a capacity limit), and drop everything
it's carrying once it's back at the dock.

A move walks the robot to its target one grid cell at a time (pausing
`MOVE_STEP_DELAY_SECONDS` between steps) rather than teleporting, so the
robot's progress is visible - e.g. in a UI polling `/status` or `/location`
mid-move - instead of jumping instantly. A move is rejected outright (leaving
the robot exactly where it was) if the straight path to the target would
cross a grid cell that currently holds product; arriving exactly on a shelf's
own location (to pick from it) is always fine, only *crossing* an occupied
cell en route to somewhere else is disallowed. Route around a blocked cell by
moving to an intermediate waypoint first.

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
| `MOVE_STEP_DELAY_SECONDS` | `0.25` | Pause between each single-grid-cell step of a move |
| `MLFLOW_TRACKING_URI` | unset | MLflow tracking server URL. When set, every MCP tool call is traced as an MLflow span (see [`src/tracing.py`](src/tracing.py)); left unset, tracing is disabled outright. MLflow's own env vars (`MLFLOW_EXPERIMENT_NAME`, `MLFLOW_WORKSPACE`, `MLFLOW_TRACKING_TOKEN`, `MLFLOW_TRACKING_AUTH`, ...) are read natively by the `mlflow` package alongside this one |

## Shelf data

[`data/shelves.csv`](data/shelves.csv) has columns `sku`, `qty`, `location_x`,
`location_y`. It's loaded into memory on startup and is the source of truth
for `POST /reset`. A SKU may appear on multiple rows to stock it at more than
one shelf location.

For the default 10x10 grid, the seed data is laid out as alternating shelf
rows and aisle rows so the robot always has a clear route rather than
threading between product scattered at random:

- Odd `y` (1, 3, 5, 7, 9) are shelf rows, each stocking four (or, for the
  last row, three) SKUs spread across `x` 1-8.
- Even `y` (0, 2, 4, 6, 8, including the dock's own row) are kept
  completely empty, forming horizontal aisles.
- `x = 0` is kept empty in every row, forming a vertical aisle from the
  dock down the left edge of the grid; `x = 9` is likewise never stocked.

A shelf row nearest the dock (`y = 1`) is directly reachable in one
straight move. Reaching a farther shelf row directly may cross a nearer
row that happens to use the same `x` column and get rejected with
`CollisionError`/400 - route around it via the `x = 0` or `x = 9` aisle and
the nearest empty cross-aisle row instead, e.g. move to `(0, y-1)`, then
`(x, y-1)`, then `(x, y)`.

## REST API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/location` | Robot's current `{x, y}` |
| `GET` | `/status` | Robot's location, carried items, and capacity |
| `POST` | `/move` | Body `{"x": int, "y": int}`; walk the robot there one cell at a time (400 if outside the grid, or if the path crosses a cell holding product) |
| `GET` | `/shelf?x=&y=` | Stock at `(x, y)`, or the robot's current location if omitted |
| `GET` | `/find/{sku}` | Every shelf location stocking `sku`, with on-hand quantity |
| `POST` | `/pick` | Body `{"sku": str, "qty": int}` (`qty > 0`); pick stock off the shelf at the robot's current location (404 unknown SKU there, 400 insufficient stock or over capacity) |
| `POST` | `/restock` | Body `{"sku": str, "qty": int, "x": int \| None, "y": int \| None}`; place newly arrived stock on a shelf - `x`/`y` must both be given or both omitted (400 otherwise); omitted auto-picks a cell already stocking `sku`, else the first empty non-dock cell (400 for an out-of-bounds or dock location, 409 if no empty cell exists) |
| `POST` | `/deliver` | Drop everything the robot is carrying (400 unless at the dock) |
| `POST` | `/reset` | Reload shelf stock from the seed CSV and return the robot to the dock, empty-handed |

```bash
curl -X POST http://localhost:8002/move -H 'Content-Type: application/json' -d '{"x": 1, "y": 1}'
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
| `get_warehouse_map` | - | Full snapshot: grid dimensions, dock, capacity, the robot's current position/carry, and every occupied shelf cell with its contents - the whole grid at once, for route planning |
| `find_item` | `sku: str` | Shelf locations stocking `sku`, with on-hand quantity at each |
| `get_shelf_inventory` | `x: int \| None`, `y: int \| None` | Everything stocked at `(x, y)`, or the robot's current location if omitted |
| `move_robot` | `x: int`, `y: int` | Walk the robot to `(x, y)` one cell at a time; fails if the path there crosses a cell holding product - route around it via a waypoint |
| `fetch_item` | `sku: str`, `qty: int` | Pick `qty` of `sku` off the shelf at the robot's current location |
| `restock_shelf` | `sku: str`, `qty: int`, `x: int \| None`, `y: int \| None` | Place newly arrived stock (e.g. from a supervisor-approved inter-DC transfer) on a shelf so it can then be found and fetched like any other stock; `x`/`y` omitted auto-picks a cell already stocking `sku`, else the first empty non-dock cell |
| `deliver_items` | - | Drop everything carried, at the dock only |
| `reset_robot` | - | Reload shelf stock and return the robot to the dock, empty-handed |

A typical agent workflow: `get_warehouse_map` once to see every occupied
shelf cell and plan a route, then per item `find_item` to confirm its SKU's
shelf location, `move_robot` there, `fetch_item` to pick it up, `move_robot`
back to the dock, then `deliver_items`. When a shortfall is resolved via an
inter-DC transfer (`supervisor-api`'s `request_transfer` tool),
`restock_shelf` places the arriving stock before it's picked and delivered
the normal way.

Connect with any MCP client that supports Streamable HTTP, e.g. the `mcp`
Python SDK's `mcp.client.streamable_http.streamable_http_client`.

## Tests

```bash
pytest
```

Covers the in-memory robot model, the REST API, and the MCP tools.
