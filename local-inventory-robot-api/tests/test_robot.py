from pathlib import Path

import pytest

from src.robot import (
    CapacityExceededError,
    InsufficientQuantityError,
    InventoryRobot,
    NotAtDockError,
    OutOfBoundsError,
    SkuNotAtLocationError,
)

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "shelves.csv"


def _robot(capacity: int = 100) -> InventoryRobot:
    return InventoryRobot(CSV_PATH, grid_width=10, grid_height=10, dock=(0, 0), capacity=capacity)


def test_starts_at_dock_empty_handed() -> None:
    robot = _robot()
    assert robot.get_location() == (0, 0)
    status = robot.get_status()
    assert status.carrying == {}
    assert status.carrying_total == 0


def test_get_shelf_stock_at_current_location() -> None:
    robot = _robot()
    robot.move_to((3, 5))
    assert robot.get_shelf_stock() == {"SKU-1001": 50}


def test_get_shelf_stock_at_given_location() -> None:
    robot = _robot()
    assert robot.get_shelf_stock((7, 2)) == {"SKU-1002": 20}


def test_get_shelf_stock_empty_location() -> None:
    robot = _robot()
    assert robot.get_shelf_stock((4, 4)) == {}


def test_find_item_returns_all_locations() -> None:
    robot = _robot()
    locations = dict(robot.find_item("SKU-1001"))
    assert locations == {(3, 5): 50, (6, 6): 10}


def test_find_item_unknown_sku_returns_empty_list() -> None:
    robot = _robot()
    assert robot.find_item("does-not-exist") == []


def test_move_to_updates_location() -> None:
    robot = _robot()
    status = robot.move_to((3, 5))
    assert (status.x, status.y) == (3, 5)
    assert robot.get_location() == (3, 5)


def test_move_out_of_bounds_raises() -> None:
    robot = _robot()
    with pytest.raises(OutOfBoundsError):
        robot.move_to((10, 0))
    with pytest.raises(OutOfBoundsError):
        robot.move_to((0, -1))


def test_pick_removes_from_shelf_and_loads_robot() -> None:
    robot = _robot()
    robot.move_to((3, 5))
    status = robot.pick("SKU-1001", 10)
    assert status.carrying == {"SKU-1001": 10}
    assert robot.get_shelf_stock() == {"SKU-1001": 40}


def test_pick_unknown_sku_at_location_raises() -> None:
    robot = _robot()
    robot.move_to((3, 5))
    with pytest.raises(SkuNotAtLocationError):
        robot.pick("SKU-1002", 1)


def test_pick_more_than_on_hand_raises_and_does_not_mutate() -> None:
    robot = _robot()
    robot.move_to((7, 2))
    with pytest.raises(InsufficientQuantityError):
        robot.pick("SKU-1002", 1000)
    assert robot.get_shelf_stock() == {"SKU-1002": 20}
    assert robot.get_status().carrying == {}


def test_pick_over_capacity_raises() -> None:
    robot = _robot(capacity=5)
    robot.move_to((3, 5))
    with pytest.raises(CapacityExceededError):
        robot.pick("SKU-1001", 10)
    assert robot.get_status().carrying == {}


def test_deliver_requires_dock() -> None:
    robot = _robot()
    robot.move_to((3, 5))
    robot.pick("SKU-1001", 10)
    with pytest.raises(NotAtDockError):
        robot.deliver()


def test_deliver_at_dock_empties_basket() -> None:
    robot = _robot()
    robot.move_to((3, 5))
    robot.pick("SKU-1001", 10)
    robot.move_to((0, 0))
    delivered, status = robot.deliver()
    assert delivered == {"SKU-1001": 10}
    assert status.carrying == {}


def test_reset_restores_shelves_and_robot_state() -> None:
    robot = _robot()
    robot.move_to((3, 5))
    robot.pick("SKU-1001", 10)
    robot.reset()
    assert robot.get_location() == (0, 0)
    assert robot.get_status().carrying == {}
    assert robot.get_shelf_stock((3, 5)) == {"SKU-1001": 50}
