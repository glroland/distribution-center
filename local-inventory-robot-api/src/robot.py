import asyncio
import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

MOVE_STEP_DELAY_SECONDS = 0.25


class OutOfBoundsError(ValueError):
    """Raised when a move target falls outside the warehouse grid."""


class CollisionError(ValueError):
    """Raised when a move's path would pass through a grid cell that currently holds product."""


class SkuNotAtLocationError(KeyError):
    """Raised when the requested SKU is not stocked at the robot's current location."""


class InsufficientQuantityError(ValueError):
    """Raised when a shelf does not have enough on-hand quantity to fulfill a pick."""


class CapacityExceededError(ValueError):
    """Raised when a pick would push the robot's carried quantity over capacity."""


class NotAtDockError(ValueError):
    """Raised when a delivery is attempted somewhere other than the dock location."""


class InvalidRestockLocationError(ValueError):
    """Raised when a restock target is out of bounds or is the dock location."""


class ShelfSpaceExhaustedError(RuntimeError):
    """Raised when no empty shelf cell is available for an auto-placed restock."""


Coordinate = tuple[int, int]


@dataclass
class RobotStatus:
    x: int
    y: int
    carrying: dict[str, int] = field(default_factory=dict)
    capacity: int = 0

    @property
    def carrying_total(self) -> int:
        return sum(self.carrying.values())


class InventoryRobot:
    """An in-memory model of a single warehouse robot moving on a 2D grid of shelves.

    Shelf stock is seeded from a CSV file and held only in memory - state resets to
    the seed data whenever `reset()` is called. The robot can move to any cell,
    pick stock off the shelf at its current cell into its own carry basket, and
    drop everything it's carrying once it reaches the dock.
    """

    def __init__(
        self,
        csv_path: Path,
        grid_width: int,
        grid_height: int,
        dock: Coordinate,
        capacity: int,
        move_step_delay: float = MOVE_STEP_DELAY_SECONDS,
    ) -> None:
        self._csv_path = csv_path
        self._grid_width = grid_width
        self._grid_height = grid_height
        self._dock = dock
        self._capacity = capacity
        self._move_step_delay = move_step_delay
        self._shelves: dict[Coordinate, dict[str, int]] = self._load_from_csv()
        self._x, self._y = dock
        self._carrying: dict[str, int] = {}

    def _load_from_csv(self) -> dict[Coordinate, dict[str, int]]:
        shelves: dict[Coordinate, dict[str, int]] = {}
        with self._csv_path.open(newline="") as f:
            for row in csv.DictReader(f):
                loc = (int(row["location_x"]), int(row["location_y"]))
                stock = shelves.setdefault(loc, {})
                stock[row["sku"]] = stock.get(row["sku"], 0) + int(row["qty"])
        logger.info("Loaded %d shelf locations from %s", len(shelves), self._csv_path)
        return shelves

    def reset(self) -> None:
        """Reload shelf stock from the seed CSV and return the robot to the dock, empty-handed."""
        logger.info("Resetting robot: reloading shelves and returning to dock %s", self._dock)
        self._shelves = self._load_from_csv()
        self._x, self._y = self._dock
        self._carrying = {}

    def get_dock(self) -> Coordinate:
        return self._dock

    def get_grid_size(self) -> tuple[int, int]:
        return self._grid_width, self._grid_height

    def get_location(self) -> Coordinate:
        return self._x, self._y

    def get_status(self) -> RobotStatus:
        return RobotStatus(
            x=self._x, y=self._y, carrying=dict(self._carrying), capacity=self._capacity
        )

    def get_shelf_stock(self, location: Coordinate | None = None) -> dict[str, int]:
        """Stock at the given grid cell, or the robot's current cell if none is given."""
        loc = location if location is not None else (self._x, self._y)
        return dict(self._shelves.get(loc, {}))

    def find_item(self, sku: str) -> list[tuple[Coordinate, int]]:
        """Every shelf location that stocks the given SKU, paired with its on-hand quantity."""
        return [
            (loc, stock[sku]) for loc, stock in self._shelves.items() if stock.get(sku, 0) > 0
        ]

    def _is_occupied(self, location: Coordinate) -> bool:
        return bool(self._shelves.get(location))

    def _plan_path(self, target: Coordinate) -> list[Coordinate]:
        """Cell-by-cell path from the current location to `target`, one grid step at
        a time (all x-axis steps first, then all y-axis steps)."""
        x, y = self._x, self._y
        target_x, target_y = target
        path: list[Coordinate] = []
        while x != target_x:
            x += 1 if target_x > x else -1
            path.append((x, y))
        while y != target_y:
            y += 1 if target_y > y else -1
            path.append((x, y))
        return path

    async def move_to(self, location: Coordinate) -> RobotStatus:
        """Walk to `location` one grid cell at a time, pausing between steps so the
        move is visible rather than instantaneous. Rejects the move outright - without
        moving the robot at all - if the target is out of bounds or the path would
        cross a cell that currently holds product; the caller must route around it."""
        x, y = location
        if not (0 <= x < self._grid_width) or not (0 <= y < self._grid_height):
            logger.warning(
                "Move to (%d, %d) rejected: outside %dx%d grid", x, y, self._grid_width, self._grid_height
            )
            raise OutOfBoundsError(
                f"({x}, {y}) is outside the {self._grid_width}x{self._grid_height} grid"
            )
        path = self._plan_path(location)
        blocked = [cell for cell in path[:-1] if self._is_occupied(cell)]
        if blocked:
            blocker = blocked[0]
            logger.warning(
                "Move to (%d, %d) rejected: path is blocked by product at %s", x, y, blocker
            )
            raise CollisionError(
                f"cannot move to ({x}, {y}): the path from ({self._x}, {self._y}) passes "
                f"through {blocker}, which currently holds product; move around it instead"
            )
        for step_x, step_y in path:
            await asyncio.sleep(self._move_step_delay)
            self._x, self._y = step_x, step_y
            logger.info("Moved to (%d, %d)", step_x, step_y)
        return self.get_status()

    def pick(self, sku: str, qty: int) -> RobotStatus:
        """Pick `qty` units of `sku` off the shelf at the robot's current location."""
        if qty <= 0:
            raise ValueError("qty must be positive")
        stock = self._shelves.get((self._x, self._y), {})
        on_hand = stock.get(sku, 0)
        if on_hand <= 0:
            logger.warning("%s not stocked at (%d, %d)", sku, self._x, self._y)
            raise SkuNotAtLocationError(sku)
        if on_hand < qty:
            logger.warning(
                "Insufficient stock for %s at (%d, %d): requested %d, only %d on hand",
                sku, self._x, self._y, qty, on_hand,
            )
            raise InsufficientQuantityError(
                f"cannot pick {qty} of {sku} at ({self._x}, {self._y}): only {on_hand} on hand"
            )
        if self.get_status().carrying_total + qty > self._capacity:
            logger.warning("Picking %d of %s would exceed carry capacity of %d", qty, sku, self._capacity)
            raise CapacityExceededError(
                f"picking {qty} of {sku} would exceed carry capacity of {self._capacity}"
            )
        stock[sku] = on_hand - qty
        if stock[sku] == 0:
            del stock[sku]
        self._carrying[sku] = self._carrying.get(sku, 0) + qty
        logger.info("Picked %d of %s at (%d, %d)", qty, sku, self._x, self._y)
        return self.get_status()

    def _find_empty_cell(self) -> Coordinate:
        """First unoccupied, non-dock cell in row-major order, for auto-placed restocks."""
        for y in range(self._grid_height):
            for x in range(self._grid_width):
                if (x, y) != self._dock and not self._is_occupied((x, y)):
                    return (x, y)
        raise ShelfSpaceExhaustedError("no empty shelf cell available to restock onto")

    def restock(self, sku: str, qty: int, location: Coordinate | None = None) -> Coordinate:
        """Add `qty` units of `sku` onto a shelf, simulating stock physically arriving
        (e.g. from an inter-DC transfer). If `location` is omitted, prefers a cell
        already stocking `sku` so it can be found by fetch as a single pile, falling
        back to the first empty non-dock cell. Fails if `qty` isn't positive, or if an
        explicit `location` is out of bounds or is the dock (which must stay clear for
        delivery)."""
        if qty <= 0:
            raise ValueError("qty must be positive")
        if location is None:
            existing = self.find_item(sku)
            location = existing[0][0] if existing else self._find_empty_cell()
        else:
            x, y = location
            if not (0 <= x < self._grid_width) or not (0 <= y < self._grid_height):
                raise InvalidRestockLocationError(
                    f"({x}, {y}) is outside the {self._grid_width}x{self._grid_height} grid"
                )
            if location == self._dock:
                raise InvalidRestockLocationError("cannot restock the dock location")
        stock = self._shelves.setdefault(location, {})
        stock[sku] = stock.get(sku, 0) + qty
        logger.info("Restocked %d of %s at %s", qty, sku, location)
        return location

    def deliver(self) -> tuple[dict[str, int], RobotStatus]:
        """Drop everything the robot is carrying, only allowed at the dock."""
        if (self._x, self._y) != self._dock:
            logger.warning(
                "Deliver rejected: robot at (%d, %d), not at dock %s", self._x, self._y, self._dock
            )
            raise NotAtDockError(
                f"robot must be at the dock {self._dock} to deliver, currently at "
                f"({self._x}, {self._y})"
            )
        delivered = dict(self._carrying)
        self._carrying = {}
        logger.info("Delivered %s at the dock", delivered)
        return delivered, self.get_status()
