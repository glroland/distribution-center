"""Loads the product catalog CSV and samples random line items from it."""

from __future__ import annotations

import csv
import random
from pathlib import Path

from src.models import LineItem

MIN_UNIT_PRICE = 1.99
MAX_UNIT_PRICE = 999.99
MIN_QUANTITY = 1
MAX_QUANTITY = 50


def load_catalog(csv_path: str | Path) -> list[dict[str, str]]:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Catalog CSV not found: {path}")

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [row for row in reader if row.get("sku") and row.get("description")]

    if not rows:
        raise ValueError(f"Catalog CSV is empty or missing sku/description columns: {path}")

    return rows


def random_line_items(
    catalog: list[dict[str, str]],
    min_items: int,
    max_items: int,
) -> list[LineItem]:
    max_items = min(max_items, len(catalog))
    min_items = min(min_items, max_items)
    count = random.randint(min_items, max_items)

    products = random.sample(catalog, count)

    return [
        LineItem(
            sku=product["sku"],
            description=product["description"],
            quantity=random.randint(MIN_QUANTITY, MAX_QUANTITY),
            unit_price=round(random.uniform(MIN_UNIT_PRICE, MAX_UNIT_PRICE), 2),
        )
        for product in products
    ]
