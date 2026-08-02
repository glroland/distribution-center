from fastmcp import FastMCP

from .robot import InventoryRobot, RobotStatus
from .tracing import configure_tracing, tool_trace

configure_tracing()


def _status_dict(status: RobotStatus) -> dict:
    return {
        "x": status.x,
        "y": status.y,
        "carrying": status.carrying,
        "capacity": status.capacity,
        "carrying_total": status.carrying_total,
    }


def build_mcp_server(robot: InventoryRobot) -> FastMCP:
    """Build a coarse-grained MCP server for LLM-driven control of a warehouse robot."""

    grid_width, grid_height = robot.get_grid_size()
    dock_x, dock_y = robot.get_dock()

    mcp_server = FastMCP(
        name="local-inventory-robot-api",
        instructions=(
            "Tools for driving a single warehouse robot around a "
            f"{grid_width}x{grid_height} grid of shelves to fetch inventory. The "
            f"robot's dock is at ({dock_x}, {dock_y}); it can only carry "
            f"{robot.get_status().capacity} total units at once and can only "
            "deliver what it's carrying once it's back at the dock. Typical "
            "workflow: for a multi-item pick run, call plan_and_fetch_items once "
            "with every SKU/qty you want fetched - it works out an efficient "
            "visiting order, moves the robot, picks each item, makes extra dock "
            "round-trips automatically if capacity would otherwise be exceeded, "
            "and delivers everything at the end. Its response reports "
            "fetched_qty per SKU, which may be less than requested_qty if a SKU "
            "isn't stocked anywhere or doesn't have enough on hand - that's not "
            "an error, just a shortfall for you to handle (e.g. via a supervisor "
            "transfer). Use get_robot_status any time to check current location "
            "and what's currently loaded, find_item or get_warehouse_map to look "
            "up stock before deciding what to request, and get_shelf_inventory "
            "to see everything stocked at a single location. Use restock_shelf "
            "when new stock physically arrives (e.g. from an inter-DC transfer) "
            "and needs to be placed on a shelf before it can be found and "
            "fetched - after restocking, call plan_and_fetch_items again for "
            "that SKU to pick it up like any other stock; omit x/y on "
            "restock_shelf to let it pick a sensible cell automatically. The "
            "lower-level move_robot/fetch_item/deliver_items tools are also "
            "available for manual control if you need it, but plan_and_fetch_items "
            "is the normal way to fulfil a pick run. Call reset_robot to restore "
            "the demo to its starting state."
        ),
    )

    @mcp_server.tool()
    @tool_trace
    def get_robot_status() -> dict:
        """Get the robot's current grid location, what it's carrying, and its capacity."""
        return _status_dict(robot.get_status())

    @mcp_server.tool()
    @tool_trace
    def find_item(sku: str) -> dict:
        """Find every shelf location that stocks a SKU, with the on-hand quantity at each."""
        return {
            "locations": [
                {"location_x": x, "location_y": y, "qty": qty}
                for (x, y), qty in robot.find_item(sku)
            ]
        }

    @mcp_server.tool()
    @tool_trace
    def get_warehouse_map() -> dict:
        """Get a full snapshot of the warehouse: grid dimensions, dock location,
        carry capacity, the robot's current position and what it's carrying, and
        every occupied shelf cell with its full contents. Unlike
        get_shelf_inventory (one cell at a time), this returns the whole grid at
        once - useful for inspecting stock before deciding what to request from
        plan_and_fetch_items, without probing cell by cell."""
        return robot.snapshot()

    @mcp_server.tool()
    @tool_trace
    def get_shelf_inventory(x: int | None = None, y: int | None = None) -> dict:
        """Get every SKU and quantity stocked at grid location (x, y), or the
        robot's current location if x and y are omitted."""
        location = (x, y) if x is not None and y is not None else None
        loc_x, loc_y = location if location is not None else robot.get_location()
        return {
            "location_x": loc_x,
            "location_y": loc_y,
            "stock": robot.get_shelf_stock(location),
        }

    @mcp_server.tool()
    @tool_trace
    async def move_robot(x: int, y: int) -> dict:
        """Move the robot directly to grid location (x, y). Fails only if the
        location is outside the grid. For a normal multi-item pick run, prefer
        plan_and_fetch_items instead of driving the robot manually."""
        return _status_dict(await robot.move_to((x, y)))

    @mcp_server.tool()
    @tool_trace
    def fetch_item(sku: str, qty: int) -> dict:
        """Pick `qty` units of `sku` off the shelf at the robot's current location and
        load them onto the robot. Fails if the SKU isn't stocked there, there isn't
        enough on hand, or it would exceed the robot's carry capacity."""
        return _status_dict(robot.pick(sku, qty))

    @mcp_server.tool()
    @tool_trace
    async def plan_and_fetch_items(items: list[dict]) -> dict:
        """Fetch a whole set of items in one call - the normal way to fulfil a
        pick run. Pass a list of {"sku": str, "qty": int} entries; the robot
        works out an efficient visiting order, moves and picks each one,
        automatically makes extra dock round-trips if carry capacity would
        otherwise be exceeded, and delivers everything once done. Returns
        {"items": [{"sku", "requested_qty", "fetched_qty"}], "trace": [...],
        "final_status": {...}}. A SKU coming back with fetched_qty below
        requested_qty is not an error - it means that SKU isn't stocked
        anywhere, or not in sufficient quantity; decide what to do about the
        shortfall yourself (e.g. request a supervisor transfer)."""
        result = await robot.run_pick_plan([(item["sku"], item["qty"]) for item in items])
        return {
            "items": result["items"],
            "trace": [
                {**step, "status": _status_dict(step["status"])} for step in result["trace"]
            ],
            "final_status": _status_dict(result["final_status"]),
        }

    @mcp_server.tool()
    @tool_trace
    def restock_shelf(sku: str, qty: int, x: int | None = None, y: int | None = None) -> dict:
        """Place `qty` newly arrived units of `sku` onto a shelf, e.g. after a
        supervisor-approved inter-DC transfer. If x and y are omitted, prefers a
        cell already stocking that SKU, otherwise picks the first empty cell.
        Fails if qty isn't positive, or if an explicit (x, y) is out of bounds or
        is the dock. Returns the resulting stock at the location used."""
        location = (x, y) if x is not None and y is not None else None
        loc_x, loc_y = robot.restock(sku, qty, location)
        return {
            "location_x": loc_x,
            "location_y": loc_y,
            "stock": robot.get_shelf_stock((loc_x, loc_y)),
        }

    @mcp_server.tool()
    @tool_trace
    def deliver_items() -> dict:
        """Drop everything the robot is carrying at the dock. Fails unless the robot
        is currently at the dock - move_robot there first."""
        delivered, status = robot.deliver()
        return {"delivered": delivered, "status": _status_dict(status)}

    @mcp_server.tool()
    @tool_trace
    def reset_robot() -> dict:
        """Reset shelf stock to the seed data and return the robot to the dock, empty-handed."""
        robot.reset()
        return {"status": "ok"}

    return mcp_server
