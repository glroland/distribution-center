import pytest
from fastapi.testclient import TestClient

from src.app import app, store


@pytest.fixture(autouse=True)
def _reset_store():
    store.reset()
    yield
    store.reset()


client = TestClient(app)


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_get_location() -> None:
    resp = client.get("/location")
    assert resp.status_code == 200
    assert resp.json() == {"location_name": store.get_location_name()}


def test_list_inventory() -> None:
    resp = client.get("/inventory")
    assert resp.status_code == 200
    skus = {item["sku"] for item in resp.json()}
    assert "SKU-1001" in skus


def test_get_inventory_item() -> None:
    resp = client.get("/inventory/SKU-1001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sku"] == "SKU-1001"
    assert body["on_hand_qty"] == 60


def test_get_inventory_item_not_found() -> None:
    resp = client.get("/inventory/does-not-exist")
    assert resp.status_code == 404


def test_increment_inventory() -> None:
    resp = client.post("/inventory/SKU-1001/increment", json={"qty": 10})
    assert resp.status_code == 200
    assert resp.json()["on_hand_qty"] == 70


def test_decrement_inventory() -> None:
    resp = client.post("/inventory/SKU-1001/decrement", json={"qty": 20})
    assert resp.status_code == 200
    assert resp.json()["on_hand_qty"] == 40


def test_decrement_below_zero_returns_400() -> None:
    resp = client.post("/inventory/SKU-1002/decrement", json={"qty": 10000})
    assert resp.status_code == 400


def test_increment_unknown_sku_returns_404() -> None:
    resp = client.post("/inventory/does-not-exist/increment", json={"qty": 1})
    assert resp.status_code == 404


def test_increment_rejects_non_positive_qty() -> None:
    resp = client.post("/inventory/SKU-1001/increment", json={"qty": 0})
    assert resp.status_code == 422


def test_boost_inventory() -> None:
    resp = client.post("/inventory/boost", json={"target_qty": 1_000_000})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["changed"] == 18

    resp = client.get("/inventory/SKU-1004")
    assert resp.json()["on_hand_qty"] == 1_000_000


def test_boost_inventory_uses_default_target() -> None:
    resp = client.post("/inventory/boost", json={})
    assert resp.status_code == 200
    resp = client.get("/inventory/SKU-1001")
    assert resp.json()["on_hand_qty"] == 1_000_000


def test_boost_inventory_rejects_non_positive_target() -> None:
    resp = client.post("/inventory/boost", json={"target_qty": 0})
    assert resp.status_code == 422


def test_reset_inventory() -> None:
    client.post("/inventory/SKU-1001/increment", json={"qty": 500})
    resp = client.post("/inventory/reset")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    resp = client.get("/inventory/SKU-1001")
    assert resp.json()["on_hand_qty"] == 60
