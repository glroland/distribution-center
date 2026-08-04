# Local Inventory Robot API

A small demo of a single warehouse robot moving on a 2D grid of shelves to
fetch inventory. Shelf stock is seeded from a CSV file and held entirely in
memory - the robot's position, what it's carrying, and shelf stock levels are
all lost on restart, or can be discarded at any time via the reset
endpoint/tool.

The robot can move to any grid cell, pick stock off the shelf at its current
cell into its own carry basket (up to a capacity limit), and drop everything
it's carrying once it's back at the dock.

A move walks the robot to its target in hops of up to 2 grid cells (pausing
`MOVE_STEP_DELAY_SECONDS` between hops) rather than teleporting, so the
robot's progress is visible - e.g. in a UI polling `/status` or `/location`
mid-move - instead of jumping instantly. There's no collision model: any
destination on the grid, including a cell that currently holds product, is
directly reachable in one call.

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
| `LABEL_API_URL` | `http://localhost:8005` | Base URL of `label-api` - `get_item_photo` fetches a picked SKU's sticker photo from here |
| `MLFLOW_TRACKING_URI` | unset | MLflow tracking server URL. When set, every MCP tool call is traced as an MLflow span (see [`src/tracing.py`](src/tracing.py)); left unset, tracing is disabled outright. MLflow's own env vars (`MLFLOW_EXPERIMENT_NAME`, `MLFLOW_WORKSPACE`, `MLFLOW_TRACKING_TOKEN`, `MLFLOW_TRACKING_AUTH`, ...) are read natively by the `mlflow` package alongside this one |

## Shelf data

[`data/shelves.csv`](data/shelves.csv) has columns `sku`, `qty`, `location_x`,
`location_y`. It's loaded into memory on startup and is the source of truth
for `POST /reset`. A SKU may appear on multiple rows to stock it at more than
one shelf location.

For the default 10x10 grid, the seed data is laid out as alternating shelf
rows and aisle rows:

- Odd `y` (1, 3, 5, 7, 9) are shelf rows, each stocking four (or, for the
  last row, three) SKUs spread across `x` 1-8.
- Even `y` (0, 2, 4, 6, 8, including the dock's own row) are kept
  completely empty, forming horizontal aisles.
- `x = 0` is kept empty in every row, forming a vertical aisle from the
  dock down the left edge of the grid; `x = 9` is likewise never stocked.

Since there's no collision model, any shelf row is directly reachable from
the dock in one straight move regardless of what's on the rows in between.

## REST API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/location` | Robot's current `{x, y}` |
| `GET` | `/status` | Robot's location, carried items, and capacity |
| `POST` | `/move` | Body `{"x": int, "y": int}`; walk the robot there (400 if outside the grid) |
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
| `plan_and_fetch_items` | `items: [{"sku": str, "qty": int}]` | Fetch a whole set of items in one call: works out an efficient visiting order, moves, picks each one, makes extra dock round-trips automatically if capacity would otherwise be exceeded, and delivers everything at the end. Returns per-item `fetched_qty` (may be less than `requested_qty` on a shortfall - not an error) and a full step-by-step trace |
| `get_robot_status` | - | Current location, carried items, and capacity |
| `get_warehouse_map` | - | Full snapshot: grid dimensions, dock, capacity, the robot's current position/carry, and every occupied shelf cell with its contents |
| `find_item` | `sku: str` | Shelf locations stocking `sku`, with on-hand quantity at each |
| `get_shelf_inventory` | `x: int \| None`, `y: int \| None` | Everything stocked at `(x, y)`, or the robot's current location if omitted |
| `move_robot` | `x: int`, `y: int` | Walk the robot directly to `(x, y)`; fails only if it's outside the grid. Manual/fallback control - prefer `plan_and_fetch_items` for a normal pick run |
| `fetch_item` | `sku: str`, `qty: int` | Pick `qty` of `sku` off the shelf at the robot's current location |
| `get_item_photo` | `sku: str` | Capture a photo of `sku`'s shelf sticker, as if by the robot's own camera - proxies `label-api`'s sticker generator and returns `{sku, image_base64, media_type}`. Feed `image_base64` to `label-api`'s `infer_sku` tool to visually verify a pick |
| `restock_shelf` | `sku: str`, `qty: int`, `x: int \| None`, `y: int \| None` | Place newly arrived stock (e.g. from a supervisor-approved inter-DC transfer) on a shelf so it can then be found and fetched like any other stock; `x`/`y` omitted auto-picks a cell already stocking `sku`, else the first empty non-dock cell |
| `deliver_items` | - | Drop everything carried, at the dock only |
| `reset_robot` | - | Reload shelf stock and return the robot to the dock, empty-handed |

A typical agent workflow: call `plan_and_fetch_items` once with every SKU/qty
needed for a pick run. When a shortfall is resolved via an inter-DC transfer
(`supervisor-api`'s `request_transfer` tool), `restock_shelf` places the
arriving stock, then another `plan_and_fetch_items` call picks it up and
delivers it the normal way. `get_warehouse_map`, `find_item`, and
`get_robot_status` remain useful for inspecting state before deciding what to
request; `move_robot`/`fetch_item`/`deliver_items` remain available for
manual control.

Connect with any MCP client that supports Streamable HTTP, e.g. the `mcp`
Python SDK's `mcp.client.streamable_http.streamable_http_client`.

Every pick (a `fetch_item` result, or a `"pick"` step in a
`plan_and_fetch_items` trace) comes back with `sticker_available: true`,
meaning `get_item_photo` can retrieve a photo of that SKU's shelf sticker (as
the robot's camera would have captured it) - `LABEL_API_URL` (default
`http://localhost:8005`) is where `get_item_photo` fetches it from. This is
what `local-dc-agent`'s fulfillment policy uses, together with `label-api`'s
`infer_sku` tool, to visually verify a pick matches the SKU it intended to
fetch before shipping it. `dashboard-ui` fetches the same photos for its own
UI preview, but over REST directly against `label-api`, not through this
tool.

## Tests

```bash
pytest
```

Covers the in-memory robot model, the REST API, and the MCP tools.
