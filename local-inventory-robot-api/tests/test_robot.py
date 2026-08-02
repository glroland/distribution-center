import asyncio
from pathlib import Path

import pytest

from src.robot import (
    CapacityExceededError,
    Coordinate,
    InsufficientQuantityError,
    InvalidRestockLocationError,
    InventoryRobot,
    NotAtDockError,
    OutOfBoundsError,
    ShelfSpaceExhaustedError,
    SkuNotAtLocationError,
)

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "shelves.csv"


def _robot(capacity: int = 100, move_step_delay: float = 0.0) -> InventoryRobot:
    return InventoryRobot(
        CSV_PATH,
        grid_width=10,
        grid_height=10,
        dock=(0, 0),
        capacity=capacity,
        move_step_delay=move_step_delay,
    )


async def _goto(robot: InventoryRobot, target: Coordinate):
    """Test-only helper: move `robot` straight to `target`."""
    return await robot.move_to(target)


def test_starts_at_dock_empty_handed() -> None:
    robot = _robot()
    assert robot.get_location() == (0, 0)
    status = robot.get_status()
    assert status.carrying == {}
    assert status.carrying_total == 0


def test_get_shelf_stock_at_current_location() -> None:
    robot = _robot()
    asyncio.run(_goto(robot, (1, 1)))
    assert robot.get_shelf_stock() == {"SKU-1001": 50}


def test_get_shelf_stock_at_given_location() -> None:
    robot = _robot()
    assert robot.get_shelf_stock((3, 1)) == {"SKU-1002": 20}


def test_get_shelf_stock_empty_location() -> None:
    robot = _robot()
    assert robot.get_shelf_stock((4, 4)) == {}


def test_find_item_returns_all_locations() -> None:
    robot = _robot()
    locations = dict(robot.find_item("SKU-1001"))
    assert locations == {(1, 1): 50, (5, 9): 10}


def test_find_item_unknown_sku_returns_empty_list() -> None:
    robot = _robot()
    assert robot.find_item("does-not-exist") == []


def test_move_to_updates_location() -> None:
    # x = 9 is never stocked, so this is a plain, unobstructed move.
    robot = _robot()
    status = asyncio.run(robot.move_to((9, 3)))
    assert (status.x, status.y) == (9, 3)
    assert robot.get_location() == (9, 3)


def test_move_lands_directly_on_a_stocked_shelf() -> None:
    robot = _robot()
    asyncio.run(robot.move_to((7, 1)))
    assert robot.get_shelf_stock() == {"SKU-1004": 40}


def test_move_out_of_bounds_raises() -> None:
    robot = _robot()
    with pytest.raises(OutOfBoundsError):
        asyncio.run(robot.move_to((10, 0)))
    with pytest.raises(OutOfBoundsError):
        asyncio.run(robot.move_to((0, -1)))


def test_move_passes_through_a_cell_that_holds_product() -> None:
    # The straight path from the dock to (1, 5) crosses (1, 1), which stocks
    # SKU-1001 - there's no collision model, so the move succeeds anyway.
    robot = _robot()
    asyncio.run(robot.move_to((1, 5)))
    assert robot.get_location() == (1, 5)
    assert robot.get_shelf_stock((1, 1)) == {"SKU-1001": 50}


def test_move_hops_two_cells_at_a_time(monkeypatch) -> None:
    robot = _robot(move_step_delay=0.25)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("src.robot.asyncio.sleep", fake_sleep)
    asyncio.run(robot.move_to((3, 0)))
    # distance 3 in x, hops of up to 2: (2,0) then (3,0) - 2 hops, not 3.
    assert sleeps == [0.25, 0.25]
    assert robot.get_location() == (3, 0)


def test_pick_removes_from_shelf_and_loads_robot() -> None:
    robot = _robot()
    asyncio.run(_goto(robot, (1, 1)))
    status = robot.pick("SKU-1001", 10)
    assert status.carrying == {"SKU-1001": 10}
    assert robot.get_shelf_stock() == {"SKU-1001": 40}


def test_pick_unknown_sku_at_location_raises() -> None:
    robot = _robot()
    asyncio.run(_goto(robot, (1, 1)))
    with pytest.raises(SkuNotAtLocationError):
        robot.pick("SKU-1002", 1)


def test_pick_more_than_on_hand_raises_and_does_not_mutate() -> None:
    robot = _robot()
    asyncio.run(_goto(robot, (3, 1)))
    with pytest.raises(InsufficientQuantityError):
        robot.pick("SKU-1002", 1000)
    assert robot.get_shelf_stock() == {"SKU-1002": 20}
    assert robot.get_status().carrying == {}


def test_pick_over_capacity_raises() -> None:
    robot = _robot(capacity=5)
    asyncio.run(_goto(robot, (1, 1)))
    with pytest.raises(CapacityExceededError):
        robot.pick("SKU-1001", 10)
    assert robot.get_status().carrying == {}


def test_deliver_requires_dock() -> None:
    robot = _robot()
    asyncio.run(_goto(robot, (1, 1)))
    robot.pick("SKU-1001", 10)
    with pytest.raises(NotAtDockError):
        robot.deliver()


def test_deliver_at_dock_empties_basket() -> None:
    robot = _robot()
    asyncio.run(_goto(robot, (1, 1)))
    robot.pick("SKU-1001", 10)
    asyncio.run(_goto(robot, (0, 0)))
    delivered, status = robot.deliver()
    assert delivered == {"SKU-1001": 10}
    assert status.carrying == {}


def test_restock_at_explicit_empty_location() -> None:
    robot = _robot()
    location = robot.restock("SKU-9999", 12, (4, 4))
    assert location == (4, 4)
    assert robot.get_shelf_stock((4, 4)) == {"SKU-9999": 12}


def test_restock_at_explicit_location_merges_with_existing_stock() -> None:
    robot = _robot()
    robot.restock("SKU-1002", 5, (3, 1))
    assert robot.get_shelf_stock((3, 1)) == {"SKU-1002": 25}


def test_restock_without_location_prefers_existing_sku_location() -> None:
    robot = _robot()
    location = robot.restock("SKU-1002", 5)
    assert location == (3, 1)
    assert robot.get_shelf_stock((3, 1)) == {"SKU-1002": 25}


def test_restock_without_location_falls_back_to_first_empty_cell() -> None:
    # Row y=0 is a fully empty aisle (including the dock at (0, 0)), so (1, 0)
    # is the first unoccupied, non-dock cell in row-major order.
    robot = _robot()
    location = robot.restock("SKU-9999", 3)
    assert location == (1, 0)
    assert robot.get_shelf_stock((1, 0)) == {"SKU-9999": 3}


def test_restock_can_then_be_found_and_fetched() -> None:
    robot = _robot()
    location = robot.restock("SKU-9999", 8, (4, 4))
    assert dict(robot.find_item("SKU-9999")) == {location: 8}
    asyncio.run(_goto(robot, location))
    status = robot.pick("SKU-9999", 8)
    assert status.carrying == {"SKU-9999": 8}


def test_restock_nonpositive_qty_raises() -> None:
    robot = _robot()
    with pytest.raises(ValueError):
        robot.restock("SKU-9999", 0, (4, 4))


def test_restock_out_of_bounds_raises() -> None:
    robot = _robot()
    with pytest.raises(InvalidRestockLocationError):
        robot.restock("SKU-9999", 1, (10, 0))


def test_restock_at_dock_raises() -> None:
    robot = _robot()
    with pytest.raises(InvalidRestockLocationError):
        robot.restock("SKU-9999", 1, (0, 0))


def test_restock_raises_when_grid_has_no_empty_cell() -> None:
    robot = InventoryRobot(
        CSV_PATH, grid_width=1, grid_height=1, dock=(0, 0), capacity=100, move_step_delay=0.0
    )
    with pytest.raises(ShelfSpaceExhaustedError):
        robot.restock("SKU-9999", 1)


def test_snapshot_reports_grid_dock_and_all_occupied_shelves() -> None:
    robot = _robot()
    snapshot = robot.snapshot()
    assert snapshot["grid_width"] == 10
    assert snapshot["grid_height"] == 10
    assert snapshot["dock"] == {"x": 0, "y": 0}
    assert snapshot["capacity"] == 100
    assert snapshot["robot"] == {"x": 0, "y": 0, "carrying": {}}
    shelves = {(cell["x"], cell["y"]): cell["stock"] for cell in snapshot["shelves"]}
    assert shelves[(1, 1)] == {"SKU-1001": 50}
    assert shelves[(3, 1)] == {"SKU-1002": 20}
    assert (4, 4) not in shelves


def test_snapshot_reflects_current_position_and_carrying() -> None:
    robot = _robot()
    asyncio.run(_goto(robot, (1, 1)))
    robot.pick("SKU-1001", 10)
    snapshot = robot.snapshot()
    assert snapshot["robot"] == {"x": 1, "y": 1, "carrying": {"SKU-1001": 10}}
    shelves = {(cell["x"], cell["y"]): cell["stock"] for cell in snapshot["shelves"]}
    assert shelves[(1, 1)] == {"SKU-1001": 40}


def test_snapshot_omits_emptied_cells() -> None:
    robot = _robot()
    asyncio.run(_goto(robot, (1, 1)))
    robot.pick("SKU-1001", 50)
    shelves = {(cell["x"], cell["y"]): cell["stock"] for cell in robot.snapshot()["shelves"]}
    assert (1, 1) not in shelves


def test_reset_restores_shelves_and_robot_state() -> None:
    robot = _robot()
    asyncio.run(_goto(robot, (1, 1)))
    robot.pick("SKU-1001", 10)
    robot.reset()
    assert robot.get_location() == (0, 0)
    assert robot.get_status().carrying == {}
    assert robot.get_shelf_stock((1, 1)) == {"SKU-1001": 50}


def test_run_pick_plan_fetches_multiple_items_and_delivers() -> None:
    robot = _robot()
    result = asyncio.run(robot.run_pick_plan([("SKU-1001", 10), ("SKU-1002", 5)]))
    items = {item["sku"]: item for item in result["items"]}
    assert items["SKU-1001"] == {"sku": "SKU-1001", "requested_qty": 10, "fetched_qty": 10}
    assert items["SKU-1002"] == {"sku": "SKU-1002", "requested_qty": 5, "fetched_qty": 5}
    assert (result["final_status"].x, result["final_status"].y) == (0, 0)
    assert result["final_status"].carrying == {}
    assert any(step["type"] == "deliver" for step in result["trace"])


def test_run_pick_plan_reports_shortfall_without_raising() -> None:
    # Only 20 units of SKU-1002 exist on any shelf.
    robot = _robot()
    result = asyncio.run(robot.run_pick_plan([("SKU-1002", 1000)]))
    assert result["items"] == [{"sku": "SKU-1002", "requested_qty": 1000, "fetched_qty": 20}]


def test_run_pick_plan_unknown_sku_reports_zero_fetched() -> None:
    robot = _robot()
    result = asyncio.run(robot.run_pick_plan([("does-not-exist", 5)]))
    assert result["items"] == [{"sku": "does-not-exist", "requested_qty": 5, "fetched_qty": 0}]


def test_run_pick_plan_makes_multiple_dock_trips_when_capacity_exceeded() -> None:
    robot = _robot(capacity=30)
    result = asyncio.run(robot.run_pick_plan([("SKU-1001", 50), ("SKU-1002", 20)]))
    items = {item["sku"]: item for item in result["items"]}
    assert items["SKU-1001"]["fetched_qty"] == 50
    assert items["SKU-1002"]["fetched_qty"] == 20
    deliver_steps = [step for step in result["trace"] if step["type"] == "deliver"]
    assert len(deliver_steps) >= 2
    assert result["final_status"].carrying == {}
    assert (result["final_status"].x, result["final_status"].y) == (0, 0)
