"""Reads the same seed CSVs local-wms-api and local-inventory-robot-api load
their in-memory stores from, so eval-suite can compute -- independently of
running the pipeline -- what a purchase order's stock-availability outcome
*should* be, without duplicating any of those services' code.

This only covers the stock-sufficiency dimension of fulfillment. It
deliberately does NOT attempt to precompute the vision-verification outcome
(robot__get_item_photo -> label__infer_sku): whether that read matches or is
low-confidence is a live model inference, not something derivable from a
CSV, so any SKU whose expected outcome depends on it is out of scope for
this module -- see fulfillment scenario docs in dataset.py.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .settings import settings

# eval-suite/src/seed_data.py -> eval-suite/src -> eval-suite -> repo root
# (correct for a full repo checkout; the Containerfile's flattened layout
# overrides this via REPO_ROOT_OVERRIDE -- see settings.py).
_DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _repo_root() -> Path:
    return Path(settings.REPO_ROOT_OVERRIDE) if settings.REPO_ROOT_OVERRIDE else _DEFAULT_REPO_ROOT


PRODUCTS_CSV = _repo_root() / "products.csv"
WMS_INVENTORY_CSV = _repo_root() / "local-wms-api" / "data" / "inventory.csv"
ROBOT_SHELVES_CSV = _repo_root() / "local-inventory-robot-api" / "data" / "shelves.csv"


@dataclass(frozen=True)
class ExpectedStock:
    sku: str
    on_hand_qty: int  # local-wms-api's ledger
    shelf_qty: int  # local-inventory-robot-api's physical shelves, summed across locations

    def expected_fulfillable(self, requested_qty: float) -> float:
        """The most a correctly-behaving pipeline could ship for this SKU: capped by
        both the WMS ledger and physical shelf stock, whichever is scarcer, and never
        more than what was requested."""
        return max(0.0, min(self.on_hand_qty, self.shelf_qty, requested_qty))


def load_catalog() -> dict[str, str]:
    """sku -> description, from the shared product catalog."""
    with PRODUCTS_CSV.open(newline="", encoding="utf-8") as f:
        return {row["sku"]: row["description"] for row in csv.DictReader(f)}


def load_expected_stock() -> dict[str, ExpectedStock]:
    """sku -> ExpectedStock for every SKU known to the catalog. A SKU present in
    products.csv but absent from inventory.csv/shelves.csv (e.g. SKU-1019/1020)
    gets zero on both -- it exists as a catalog entry but was never stocked."""
    on_hand: dict[str, int] = {}
    with WMS_INVENTORY_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            on_hand[row["sku"]] = int(row["on_hand_qty"])

    shelf: dict[str, int] = {}
    with ROBOT_SHELVES_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            # A SKU can occupy more than one shelf location (e.g. SKU-1001 has two
            # rows); its total pickable stock is the sum across all of them.
            shelf[row["sku"]] = shelf.get(row["sku"], 0) + int(row["qty"])

    return {
        sku: ExpectedStock(sku=sku, on_hand_qty=on_hand.get(sku, 0), shelf_qty=shelf.get(sku, 0))
        for sku in load_catalog()
    }
