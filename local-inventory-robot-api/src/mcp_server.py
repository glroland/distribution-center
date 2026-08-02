from mcp.server import MCPServer

from .robot import InventoryRobot, RobotStatus


def _status_dict(status: RobotStatus) -> dict:
    return {
        "x": status.x,
        "y": status.y,
        "carrying": status.carrying,
        "capacity": status.capacity,
        "carrying_total": status.carrying_total,
    }


def build_mcp_server(robot: InventoryRobot) -> MCPServer:
    """Build a coarse-grained MCP server for LLM-driven control of a warehouse robot."""

    grid_width, grid_height = robot.get_grid_size()
    dock_x, dock_y = robot.get_dock()

    mcp_server = MCPServer(
        name="local-inventory-robot-api",
        instructions=(
            "Tools for driving a single warehouse robot around a "
            f"{grid_width}x{grid_height} grid of shelves to fetch inventory. The "
            f"robot's dock is at ({dock_x}, {dock_y}); it can only carry "
            f"{robot.get_status().capacity} total units at once and can only "
            "deliver what it's carrying once it's back at the dock. Typical "
            "workflow: call find_item to see which shelf locations stock a SKU, "
            "move_robot there, fetch_item to pick it off the shelf, move_robot "
            "back to the dock, then deliver_items. Use get_robot_status any time "
            "to check current location and what's currently loaded, and "
            "get_shelf_inventory to see everything stocked at a location. Use "
            "restock_shelf when new stock physically arrives (e.g. from an inter-DC "
            "transfer) and needs to be placed on a shelf before it can be found and "
            "fetched like any other stock; omit x/y to let it pick a sensible cell "
            "automatically. "
            "move_robot physically drives the robot there one grid cell at a time "
            "(it will not teleport), so it's fine to call it with any destination on "
            "the grid - the robot itself is only rejected if the straight path there "
            "would cross a cell that currently holds product; if that happens, retry "
            "with a different route, e.g. move_robot to an intermediate waypoint "
            "first and continue from there. Arriving exactly at a shelf's own "
            "location (to fetch_item from it) is always allowed. Call reset_robot to "
            "restore the demo to its starting state."
        ),
    )

    @mcp_server.tool()
    def get_robot_status() -> dict:
        """Get the robot's current grid location, what it's carrying, and its capacity."""
        return _status_dict(robot.get_status())

    @mcp_server.tool()
    def find_item(sku: str) -> dict:
        """Find every shelf location that stocks a SKU, with the on-hand quantity at each."""
        return {
            "locations": [
                {"location_x": x, "location_y": y, "qty": qty}
                for (x, y), qty in robot.find_item(sku)
            ]
        }

    @mcp_server.tool()
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
    async def move_robot(x: int, y: int) -> dict:
        """Move the robot to grid location (x, y), one grid cell at a time. Fails if
        the location is outside the grid, or if the path there would cross a cell
        that currently holds product - pick a different route (e.g. an intermediate
        waypoint) and try again. Arriving exactly at a shelf's location to pick from
        it is always fine."""
        return _status_dict(await robot.move_to((x, y)))

    @mcp_server.tool()
    def fetch_item(sku: str, qty: int) -> dict:
        """Pick `qty` units of `sku` off the shelf at the robot's current location and
        load them onto the robot. Fails if the SKU isn't stocked there, there isn't
        enough on hand, or it would exceed the robot's carry capacity."""
        return _status_dict(robot.pick(sku, qty))

    @mcp_server.tool()
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
    def deliver_items() -> dict:
        """Drop everything the robot is carrying at the dock. Fails unless the robot
        is currently at the dock - move_robot there first."""
        delivered, status = robot.deliver()
        return {"delivered": delivered, "status": _status_dict(status)}

    @mcp_server.tool()
    def reset_robot() -> dict:
        """Reset shelf stock to the seed data and return the robot to the dock, empty-handed."""
        robot.reset()
        return {"status": "ok"}

    return mcp_server
