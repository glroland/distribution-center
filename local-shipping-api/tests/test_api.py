import pytest
from fastapi.testclient import TestClient

from src.app import app, store

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_store():
    store.reset()
    yield
    store.reset()


def _create_shipment(**overrides) -> dict:
    body = {
        "po_number": "PO-1001",
        "customer_name": "Jane Doe",
        "customer_address": "123 Main St, Springfield",
        "items": [{"sku": "SKU-1001", "qty": 10}],
    }
    body.update(overrides)
    return client.post("/shipments", json=body).json()


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_create_shipment() -> None:
    resp = client.post(
        "/shipments",
        json={
            "po_number": "PO-1001",
            "customer_name": "Jane Doe",
            "customer_address": "123 Main St, Springfield",
            "items": [{"sku": "SKU-1001", "qty": 10}, {"sku": "SKU-1002", "qty": 5}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 1
    assert body["po_number"] == "PO-1001"
    assert body["items"] == [{"sku": "SKU-1001", "qty": 10}, {"sku": "SKU-1002", "qty": 5}]
    assert body["status"] == "shipped"
    assert body["carrier"] in {"UPS", "FedEx", "USPS", "DHL"}
    assert body["tracking_number"]


def test_create_shipment_rejects_blank_po_number() -> None:
    resp = client.post(
        "/shipments",
        json={
            "po_number": "",
            "customer_name": "Jane Doe",
            "customer_address": "123 Main St",
            "items": [{"sku": "SKU-1001", "qty": 10}],
        },
    )
    assert resp.status_code == 422


def test_create_shipment_rejects_empty_items() -> None:
    resp = client.post(
        "/shipments",
        json={
            "po_number": "PO-1001",
            "customer_name": "Jane Doe",
            "customer_address": "123 Main St",
            "items": [],
        },
    )
    assert resp.status_code == 422


def test_create_shipment_rejects_non_positive_qty() -> None:
    resp = client.post(
        "/shipments",
        json={
            "po_number": "PO-1001",
            "customer_name": "Jane Doe",
            "customer_address": "123 Main St",
            "items": [{"sku": "SKU-1001", "qty": 0}],
        },
    )
    assert resp.status_code == 422


def test_list_shipments_empty() -> None:
    resp = client.get("/shipments")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_shipments() -> None:
    _create_shipment(po_number="PO-1001")
    _create_shipment(po_number="PO-1002")
    resp = client.get("/shipments")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_list_shipments_filters_by_po_number() -> None:
    first = _create_shipment(po_number="PO-1001")
    _create_shipment(po_number="PO-1002")
    resp = client.get("/shipments", params={"po_number": "PO-1001"})
    assert resp.status_code == 200
    assert [s["id"] for s in resp.json()] == [first["id"]]


def test_get_shipment() -> None:
    created = _create_shipment()
    resp = client.get(f"/shipments/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_shipment_not_found() -> None:
    resp = client.get("/shipments/999")
    assert resp.status_code == 404


def test_get_shipment_by_tracking() -> None:
    created = _create_shipment()
    resp = client.get(f"/shipments/tracking/{created['tracking_number']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_shipment_by_tracking_not_found() -> None:
    resp = client.get("/shipments/tracking/does-not-exist")
    assert resp.status_code == 404


def test_reset() -> None:
    _create_shipment()
    resp = client.post("/shipments/reset")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "shipment_count": 0}
    assert client.get("/shipments").json() == []
