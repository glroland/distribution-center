import asyncio

import httpx

from .models import DistributionCenter


async def scan_shelf_grid(dc: DistributionCenter) -> dict:
    """Scans every cell of the robot's grid (there's no bulk-listing endpoint on
    local-inventory-robot-api) to build a static base map of shelf contents, plus
    the robot's live position/capacity. Cheap for the default 10x10 grid; run once
    on demand rather than on every poll."""
    cells: list[dict] = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        status_response = await client.get(f"{dc.robot_url}/status")
        status_response.raise_for_status()
        status = status_response.json()

        async def fetch_cell(x: int, y: int) -> None:
            response = await client.get(f"{dc.robot_url}/shelf", params={"x": x, "y": y})
            response.raise_for_status()
            body = response.json()
            if body.get("stock"):
                cells.append({"x": x, "y": y, "stock": body["stock"]})

        tasks = [
            fetch_cell(x, y)
            for x in range(dc.grid_width)
            for y in range(dc.grid_height)
        ]
        await asyncio.gather(*tasks)

    cells.sort(key=lambda cell: (cell["x"], cell["y"]))
    return {
        "width": dc.grid_width,
        "height": dc.grid_height,
        "dock": {"x": dc.dock_x, "y": dc.dock_y},
        "cells": cells,
        "robot": status,
    }
