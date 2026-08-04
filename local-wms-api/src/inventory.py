import csv
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class SkuNotFoundError(KeyError):
    """Raised when a SKU is not present in the inventory."""


class InsufficientQuantityError(ValueError):
    """Raised when a decrement would take on-hand quantity below zero."""


@dataclass
class InventoryItem:
    sku: str
    on_hand_qty: int
    location_x: int
    location_y: int


class InventoryStore:
    """In-memory inventory for a single virtual location, seeded from a CSV file."""

    def __init__(self, csv_path: Path, location_name: str) -> None:
        self._csv_path = csv_path
        self._location_name = location_name
        self._items: dict[str, InventoryItem] = self._load_from_csv()

    def get_location_name(self) -> str:
        return self._location_name

    def reset(self) -> None:
        """Reload inventory from the original CSV, discarding all in-memory changes."""
        logger.info("Resetting inventory from %s", self._csv_path)
        self._items = self._load_from_csv()

    def _load_from_csv(self) -> dict[str, InventoryItem]:
        items: dict[str, InventoryItem] = {}
        with self._csv_path.open(newline="") as f:
            for row in csv.DictReader(f):
                items[row["sku"]] = InventoryItem(
                    sku=row["sku"],
                    on_hand_qty=int(row["on_hand_qty"]),
                    location_x=int(row["location_x"]),
                    location_y=int(row["location_y"]),
                )
        logger.info("Loaded %d inventory items from %s", len(items), self._csv_path)
        return items

    def list_items(self) -> list[InventoryItem]:
        return list(self._items.values())

    def get_item(self, sku: str) -> InventoryItem:
        try:
            return self._items[sku]
        except KeyError:
            logger.warning("Unknown SKU requested: %s", sku)
            raise SkuNotFoundError(sku) from None

    def get_quantity(self, sku: str) -> int:
        return self.get_item(sku).on_hand_qty

    def increment(self, sku: str, qty: int) -> InventoryItem:
        if qty <= 0:
            raise ValueError("qty must be positive")
        item = self.get_item(sku)
        item.on_hand_qty += qty
        logger.info("Incremented %s by %d -> %d on hand", sku, qty, item.on_hand_qty)
        return item

    def boost(self, target_qty: int) -> int:
        """Raise every item's on-hand quantity up to at least `target_qty`, leaving
        items already at or above it untouched. Returns the number of items changed."""
        changed = 0
        for item in self._items.values():
            if item.on_hand_qty < target_qty:
                item.on_hand_qty = target_qty
                changed += 1
        logger.info("Boosted inventory: %d items raised to >= %d", changed, target_qty)
        return changed

    def decrement(self, sku: str, qty: int) -> InventoryItem:
        if qty <= 0:
            raise ValueError("qty must be positive")
        item = self.get_item(sku)
        if item.on_hand_qty - qty < 0:
            logger.warning(
                "Insufficient quantity for %s: cannot decrement by %d, only %d on hand",
                sku, qty, item.on_hand_qty,
            )
            raise InsufficientQuantityError(
                f"cannot decrement {sku} by {qty}: only {item.on_hand_qty} on hand"
            )
        item.on_hand_qty -= qty
        logger.info("Decremented %s by %d -> %d on hand", sku, qty, item.on_hand_qty)
        return item
