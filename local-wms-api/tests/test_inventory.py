from pathlib import Path

import pytest

from src.inventory import InsufficientQuantityError, InventoryStore, SkuNotFoundError

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "inventory.csv"


def _store() -> InventoryStore:
    return InventoryStore(CSV_PATH, "TEST-DC")


def test_loads_initial_inventory_from_csv() -> None:
    store = _store()
    item = store.get_item("SKU-1001")
    assert item.on_hand_qty == 60
    assert item.location_x == 3
    assert item.location_y == 5


def test_get_quantity() -> None:
    store = _store()
    assert store.get_quantity("SKU-1002") == 20


def test_get_quantity_unknown_sku_raises() -> None:
    store = _store()
    with pytest.raises(SkuNotFoundError):
        store.get_quantity("does-not-exist")


def test_get_location_name() -> None:
    store = _store()
    assert store.get_location_name() == "TEST-DC"


def test_increment() -> None:
    store = _store()
    item = store.increment("SKU-1001", 10)
    assert item.on_hand_qty == 70
    assert store.get_quantity("SKU-1001") == 70


def test_decrement() -> None:
    store = _store()
    item = store.decrement("SKU-1001", 20)
    assert item.on_hand_qty == 40


def test_decrement_below_zero_raises() -> None:
    store = _store()
    with pytest.raises(InsufficientQuantityError):
        store.decrement("SKU-1002", 1000)
    # the failed decrement must not have mutated state
    assert store.get_quantity("SKU-1002") == 20


def test_increment_unknown_sku_raises() -> None:
    store = _store()
    with pytest.raises(SkuNotFoundError):
        store.increment("does-not-exist", 1)


def test_increment_non_positive_qty_raises() -> None:
    store = _store()
    with pytest.raises(ValueError):
        store.increment("SKU-1001", 0)


def test_reset_restores_initial_state() -> None:
    store = _store()
    store.increment("SKU-1001", 500)
    store.decrement("SKU-1002", 10)
    store.reset()
    assert store.get_quantity("SKU-1001") == 60
    assert store.get_quantity("SKU-1002") == 20


def test_list_items_returns_all_skus() -> None:
    store = _store()
    skus = {item.sku for item in store.list_items()}
    assert skus == {
        "SKU-1001",
        "SKU-1002",
        "SKU-1003",
        "SKU-1004",
        "SKU-1005",
        "SKU-1006",
        "SKU-1007",
        "SKU-1008",
        "SKU-1009",
        "SKU-1010",
        "SKU-1011",
        "SKU-1012",
        "SKU-1013",
        "SKU-1014",
        "SKU-1015",
        "SKU-1016",
        "SKU-1017",
        "SKU-1018",
    }
