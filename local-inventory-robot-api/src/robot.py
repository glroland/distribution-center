import asyncio
import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

MOVE_STEP_DELAY_SECONDS = 0.25
MOVE_STEP_STRIDE = 2


class OutOfBoundsError(ValueError):
    """Raised when a move target falls outside the warehouse grid."""


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

    def snapshot(self) -> dict:
        """A full picture of the warehouse: grid dimensions, dock, capacity, the
        robot's current position/carry, and every occupied shelf cell with its
        contents. Unlike `get_shelf_stock`, this returns every stocked cell at
        once so a caller can plan a whole route instead of probing cell by cell."""
        return {
            "grid_width": self._grid_width,
            "grid_height": self._grid_height,
            "dock": {"x": self._dock[0], "y": self._dock[1]},
            "capacity": self._capacity,
            "robot": {"x": self._x, "y": self._y, "carrying": dict(self._carrying)},
            "shelves": [
                {"x": x, "y": y, "stock": dict(stock)}
                for (x, y), stock in sorted(self._shelves.items())
                if stock
            ],
        }

    def find_item(self, sku: str) -> list[tuple[Coordinate, int]]:
        """Every shelf location that stocks the given SKU, paired with its on-hand quantity."""
        return [
            (loc, stock[sku]) for loc, stock in self._shelves.items() if stock.get(sku, 0) > 0
        ]

    def _is_occupied(self, location: Coordinate) -> bool:
        return bool(self._shelves.get(location))

    def _plan_path(self, target: Coordinate) -> list[Coordinate]:
        """Hop-by-hop path from the current location to `target`, moving up to
        `MOVE_STEP_STRIDE` grid cells per hop (all x-axis hops first, then all
        y-axis hops), with the final hop on each axis shortened to land exactly
        on `target`."""
        x, y = self._x, self._y
        target_x, target_y = target
        path: list[Coordinate] = []
        while x != target_x:
            step = min(MOVE_STEP_STRIDE, abs(target_x - x))
            x += step if target_x > x else -step
            path.append((x, y))
        while y != target_y:
            step = min(MOVE_STEP_STRIDE, abs(target_y - y))
            y += step if target_y > y else -step
            path.append((x, y))
        return path

    async def move_to(self, location: Coordinate) -> RobotStatus:
        """Walk to `location`, pausing between hops so the move is visible rather
        than instantaneous. Rejects the move outright - without moving the robot
        at all - if the target is out of bounds. There's no collision to worry
        about: any destination on the grid is reachable directly."""
        x, y = location
        if not (0 <= x < self._grid_width) or not (0 <= y < self._grid_height):
            logger.warning(
                "Move to (%d, %d) rejected: outside %dx%d grid", x, y, self._grid_width, self._grid_height
            )
            raise OutOfBoundsError(
                f"({x}, {y}) is outside the {self._grid_width}x{self._grid_height} grid"
            )
        for step_x, step_y in self._plan_path(location):
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

    def boost_shelves(self, target_qty: int) -> int:
        """Raise every currently-stocked shelf slot up to at least `target_qty`,
        leaving slots already at or above it untouched. Returns the number of
        slots changed."""
        changed = 0
        for stock in self._shelves.values():
            for sku, qty in stock.items():
                if qty < target_qty:
                    stock[sku] = target_qty
                    changed += 1
        logger.info("Boosted shelves: %d slots raised to >= %d", changed, target_qty)
        return changed

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

    async def run_pick_plan(self, items: list[tuple[str, int]]) -> dict:
        """Fetch every requested (sku, qty) pair in one go: resolve each SKU to
        its shelf locations, visit them in an efficient order, pick as much as
        is on hand at each (up to what's still needed and what carry capacity
        allows, running back to the dock to deliver whenever it would otherwise
        overflow), and deliver whatever's left once the whole plan is done.
        Never raises for a shortfall - if a SKU comes up short or isn't stocked
        anywhere, the returned per-item `fetched_qty` is simply less than
        `requested_qty`, leaving what to do about it up to the caller."""
        requested: dict[str, int] = {}
        for sku, qty in items:
            requested[sku] = requested.get(sku, 0) + qty
        remaining = dict(requested)
        fetched = {sku: 0 for sku in requested}

        pool: list[tuple[Coordinate, str]] = [
            (loc, sku) for sku in requested for loc, _ in self.find_item(sku)
        ]
        trace: list[dict] = []

        async def _move(target: Coordinate) -> None:
            if (self._x, self._y) == target:
                return
            status = await self.move_to(target)
            trace.append({"type": "move", "x": target[0], "y": target[1], "status": status})

        async def _return_and_deliver() -> None:
            await _move(self._dock)
            if self._carrying:
                delivered, status = self.deliver()
                trace.append({"type": "deliver", "delivered": delivered, "status": status})

        cur = (self._x, self._y)
        while pool:
            pool.sort(key=lambda s: abs(s[0][0] - cur[0]) + abs(s[0][1] - cur[1]))
            location, sku = pool.pop(0)
            need = remaining.get(sku, 0)
            available = self.get_shelf_stock(location).get(sku, 0)
            if need <= 0 or available <= 0:
                continue
            room = self._capacity - self.get_status().carrying_total
            if room <= 0:
                await _return_and_deliver()
                cur = self._dock
                room = self._capacity
            take = min(need, available, room)
            if take <= 0:
                continue
            if take < min(need, available):
                pool.append((location, sku))
            await _move(location)
            status = self.pick(sku, take)
            trace.append(
                {"type": "pick", "x": location[0], "y": location[1], "sku": sku, "qty": take, "status": status}
            )
            remaining[sku] -= take
            fetched[sku] += take
            cur = location

        await _return_and_deliver()

        return {
            "items": [
                {"sku": sku, "requested_qty": qty, "fetched_qty": fetched[sku]}
                for sku, qty in requested.items()
            ],
            "trace": trace,
            "final_status": self.get_status(),
        }
