import logging

import httpx
from mcp.server.fastmcp import FastMCP as MCPServer

from .prompts import get_prompt
from .robot import InventoryRobot, RobotStatus
from .settings import settings
from .tracing import configure_tracing, tool_trace

configure_tracing()

logger = logging.getLogger(__name__)


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

    instructions = get_prompt("local-inventory-robot-api.mcp_server.instructions").format(
        grid_width=grid_width,
        grid_height=grid_height,
        dock_x=dock_x,
        dock_y=dock_y,
        capacity=robot.get_status().capacity,
    )
    mcp_server = MCPServer(
        name="local-inventory-robot-api",
        instructions=instructions,
        host=settings.HOST,
        streamable_http_path="/",
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
        enough on hand, or it would exceed the robot's carry capacity. The response
        includes sticker_available: true - a photo of the SKU's shelf sticker, as the
        robot's camera would have captured it, can be retrieved with get_item_photo
        and checked against label-api's infer_sku tool to verify the pick."""
        return {**_status_dict(robot.pick(sku, qty)), "sticker_available": True}

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
        shortfall yourself (e.g. request a supervisor transfer). Each "pick" step
        in trace includes sticker_available: true - a photo of that SKU's shelf
        sticker, as the robot's camera would have captured it, can be retrieved
        with get_item_photo and checked against label-api's infer_sku tool to
        verify the pick."""
        result = await robot.run_pick_plan([(item["sku"], item["qty"]) for item in items])
        return {
            "items": result["items"],
            "trace": [
                {
                    **step,
                    "status": _status_dict(step["status"]),
                    **({"sticker_available": True} if step["type"] == "pick" else {}),
                }
                for step in result["trace"]
            ],
            "final_status": _status_dict(result["final_status"]),
        }

    @mcp_server.tool()
    @tool_trace
    async def get_item_photo(sku: str) -> dict:
        """Capture a photo of `sku`'s shelf sticker, as if by the robot's own
        camera - call this right after picking a SKU (sticker_available: true
        on the pick result) to get an image to visually verify the pick with.
        Returns {"sku", "image_id", "media_type"}; pass image_id straight to
        label-api's infer_sku tool - if what it reads back doesn't match the
        SKU you intended to fetch, or its confidence is low, that's a real
        mispick or mislabeled-shelf signal, not something to ignore. The
        photo itself is stored on label-api, keyed by image_id - never pass
        image bytes directly, only the id. Fails if label-api is unreachable
        or returns an error."""
        url = f"{settings.LABEL_API_URL}/stickers/{sku}/capture"
        logger.info("Capturing shelf sticker photo for %s via %s", sku, url)
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url)
        if response.status_code != 200:
            logger.warning(
                "Photo capture failed for %s: label-api returned %d: %s",
                sku, response.status_code, response.text,
            )
            raise RuntimeError(
                f"label-api returned {response.status_code} generating a photo for {sku}: {response.text}"
            )
        body = response.json()
        return {
            "sku": body["sku"],
            "image_id": body["image_id"],
            "media_type": body["media_type"],
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
