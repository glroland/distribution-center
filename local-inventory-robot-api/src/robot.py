import csv
from dataclasses import dataclass, field
from pathlib import Path


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
    ) -> None:
        self._csv_path = csv_path
        self._grid_width = grid_width
        self._grid_height = grid_height
        self._dock = dock
        self._capacity = capacity
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
        return shelves

    def reset(self) -> None:
        """Reload shelf stock from the seed CSV and return the robot to the dock, empty-handed."""
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

    def move_to(self, location: Coordinate) -> RobotStatus:
        x, y = location
        if not (0 <= x < self._grid_width) or not (0 <= y < self._grid_height):
            raise OutOfBoundsError(
                f"({x}, {y}) is outside the {self._grid_width}x{self._grid_height} grid"
            )
        self._x, self._y = x, y
        return self.get_status()

    def pick(self, sku: str, qty: int) -> RobotStatus:
        """Pick `qty` units of `sku` off the shelf at the robot's current location."""
        if qty <= 0:
            raise ValueError("qty must be positive")
        stock = self._shelves.get((self._x, self._y), {})
        on_hand = stock.get(sku, 0)
        if on_hand <= 0:
            raise SkuNotAtLocationError(sku)
        if on_hand < qty:
            raise InsufficientQuantityError(
                f"cannot pick {qty} of {sku} at ({self._x}, {self._y}): only {on_hand} on hand"
            )
        if self.get_status().carrying_total + qty > self._capacity:
            raise CapacityExceededError(
                f"picking {qty} of {sku} would exceed carry capacity of {self._capacity}"
            )
        stock[sku] = on_hand - qty
        if stock[sku] == 0:
            del stock[sku]
        self._carrying[sku] = self._carrying.get(sku, 0) + qty
        return self.get_status()

    def deliver(self) -> tuple[dict[str, int], RobotStatus]:
        """Drop everything the robot is carrying, only allowed at the dock."""
        if (self._x, self._y) != self._dock:
            raise NotAtDockError(
                f"robot must be at the dock {self._dock} to deliver, currently at "
                f"({self._x}, {self._y})"
            )
        delivered = dict(self._carrying)
        self._carrying = {}
        return delivered, self.get_status()
