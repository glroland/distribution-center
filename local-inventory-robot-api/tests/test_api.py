import pytest
from fastapi.testclient import TestClient

from src.app import app, robot

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_robot():
    robot.reset()
    robot._move_step_delay = 0
    yield
    robot.reset()


def _goto(x: int, y: int) -> None:
    """Test-only helper: move the robot straight to (x, y) via POST /move."""
    resp = client.post("/move", json={"x": x, "y": y})
    assert resp.status_code == 200


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_get_location() -> None:
    resp = client.get("/location")
    assert resp.status_code == 200
    assert resp.json() == {"x": 0, "y": 0}


def test_get_status() -> None:
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["x"] == 0
    assert body["y"] == 0
    assert body["carrying"] == {}
    assert body["carrying_total"] == 0


def test_move() -> None:
    # x = 9 is never stocked, so this is a plain, unobstructed move.
    resp = client.post("/move", json={"x": 9, "y": 3})
    assert resp.status_code == 200
    assert resp.json()["x"] == 9
    assert resp.json()["y"] == 3


def test_move_out_of_bounds_returns_400() -> None:
    resp = client.post("/move", json={"x": 99, "y": 0})
    assert resp.status_code == 400


def test_get_shelf_at_location() -> None:
    resp = client.get("/shelf", params={"x": 1, "y": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["location_x"] == 1
    assert body["location_y"] == 1
    assert body["stock"] == {"SKU-1001": 50}


def test_get_shelf_at_current_location() -> None:
    _goto(3, 1)
    resp = client.get("/shelf")
    assert resp.status_code == 200
    assert resp.json()["stock"] == {"SKU-1002": 20}


def test_get_shelf_requires_both_coordinates() -> None:
    resp = client.get("/shelf", params={"x": 3})
    assert resp.status_code == 400


def test_find_item() -> None:
    resp = client.get("/find/SKU-1001")
    assert resp.status_code == 200
    locations = {(loc["location_x"], loc["location_y"]): loc["qty"] for loc in resp.json()}
    assert locations == {(1, 1): 50, (5, 9): 10}


def test_find_item_unknown_sku_returns_empty_list() -> None:
    resp = client.get("/find/does-not-exist")
    assert resp.status_code == 200
    assert resp.json() == []


def test_pick_and_deliver_round_trip() -> None:
    _goto(1, 1)
    resp = client.post("/pick", json={"sku": "SKU-1001", "qty": 10})
    assert resp.status_code == 200
    assert resp.json()["carrying"] == {"SKU-1001": 10}

    _goto(0, 0)
    resp = client.post("/deliver")
    assert resp.status_code == 200
    body = resp.json()
    assert body["delivered"] == {"SKU-1001": 10}
    assert body["status"]["carrying"] == {}


def test_pick_unknown_sku_returns_404() -> None:
    _goto(1, 1)
    resp = client.post("/pick", json={"sku": "does-not-exist", "qty": 1})
    assert resp.status_code == 404


def test_pick_insufficient_quantity_returns_400() -> None:
    _goto(3, 1)
    resp = client.post("/pick", json={"sku": "SKU-1002", "qty": 10000})
    assert resp.status_code == 400


def test_pick_rejects_non_positive_qty() -> None:
    _goto(1, 1)
    resp = client.post("/pick", json={"sku": "SKU-1001", "qty": 0})
    assert resp.status_code == 422


def test_deliver_away_from_dock_returns_400() -> None:
    _goto(1, 1)
    client.post("/pick", json={"sku": "SKU-1001", "qty": 5})
    resp = client.post("/deliver")
    assert resp.status_code == 400


def test_restock_at_explicit_location() -> None:
    resp = client.post("/restock", json={"sku": "SKU-9999", "qty": 12, "x": 4, "y": 4})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"location_x": 4, "location_y": 4, "stock": {"SKU-9999": 12}}


def test_restock_without_location_prefers_existing_sku_location() -> None:
    resp = client.post("/restock", json={"sku": "SKU-1002", "qty": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"location_x": 3, "location_y": 1, "stock": {"SKU-1002": 25}}


def test_restock_then_pick_round_trip() -> None:
    client.post("/restock", json={"sku": "SKU-9999", "qty": 8, "x": 4, "y": 4})
    _goto(4, 4)
    resp = client.post("/pick", json={"sku": "SKU-9999", "qty": 8})
    assert resp.status_code == 200
    assert resp.json()["carrying"] == {"SKU-9999": 8}


def test_restock_requires_both_coordinates() -> None:
    resp = client.post("/restock", json={"sku": "SKU-9999", "qty": 1, "x": 4})
    assert resp.status_code == 400


def test_restock_at_dock_returns_400() -> None:
    resp = client.post("/restock", json={"sku": "SKU-9999", "qty": 1, "x": 0, "y": 0})
    assert resp.status_code == 400


def test_restock_out_of_bounds_returns_400() -> None:
    resp = client.post("/restock", json={"sku": "SKU-9999", "qty": 1, "x": 99, "y": 0})
    assert resp.status_code == 400


def test_restock_rejects_non_positive_qty() -> None:
    resp = client.post("/restock", json={"sku": "SKU-9999", "qty": 0, "x": 4, "y": 4})
    assert resp.status_code == 422


def test_reset() -> None:
    _goto(1, 1)
    client.post("/pick", json={"sku": "SKU-1001", "qty": 10})
    resp = client.post("/reset")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    assert client.get("/location").json() == {"x": 0, "y": 0}
    assert client.get("/shelf", params={"x": 1, "y": 1}).json()["stock"] == {"SKU-1001": 50}
