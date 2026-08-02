import pytest
from fastapi.testclient import TestClient

from src.app import app, store


@pytest.fixture(autouse=True)
def _reset_store():
    original_unavailable_chance = store._unavailable_chance
    store.reset()
    yield
    store.reset()
    store._unavailable_chance = original_unavailable_chance


client = TestClient(app)


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_list_help_requests_empty() -> None:
    resp = client.get("/help-requests")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_help_requests() -> None:
    store.create_help_request("stuck on step 1", agent_id="agent-1")
    store.create_help_request("stuck on step 2", agent_id="agent-2")

    resp = client.get("/help-requests")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert {r["question"] for r in body} == {"stuck on step 1", "stuck on step 2"}


def test_list_help_requests_filters_by_status() -> None:
    open_request = store.create_help_request("open question")
    resolved_request = store.create_help_request("resolved question")
    store.resolve_help_request(resolved_request.id, "answer")

    resp = client.get("/help-requests", params={"status": "open"})
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [open_request.id]


def test_list_help_requests_rejects_invalid_status() -> None:
    resp = client.get("/help-requests", params={"status": "bogus"})
    assert resp.status_code == 400


def test_get_help_request() -> None:
    created = store.create_help_request("what next?", context="tried X and Y")

    resp = client.get(f"/help-requests/{created.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created.id
    assert body["question"] == "what next?"
    assert body["context"] == "tried X and Y"
    assert body["status"] == "open"


def test_get_help_request_not_found() -> None:
    resp = client.get("/help-requests/999")
    assert resp.status_code == 404


def test_resolve_help_request() -> None:
    created = store.create_help_request("what next?")

    resp = client.post(f"/help-requests/{created.id}/resolve", json={"resolution": "do Z"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["resolution"] == "do Z"
    assert body["resolved_at"] is not None


def test_resolve_help_request_not_found() -> None:
    resp = client.post("/help-requests/999/resolve", json={"resolution": "do Z"})
    assert resp.status_code == 404


def test_resolve_help_request_already_resolved_returns_400() -> None:
    created = store.create_help_request("what next?")
    client.post(f"/help-requests/{created.id}/resolve", json={"resolution": "first"})

    resp = client.post(f"/help-requests/{created.id}/resolve", json={"resolution": "second"})
    assert resp.status_code == 400


def test_resolve_help_request_rejects_blank_resolution() -> None:
    created = store.create_help_request("what next?")
    resp = client.post(f"/help-requests/{created.id}/resolve", json={"resolution": ""})
    assert resp.status_code == 422


def test_list_transfer_requests_empty() -> None:
    resp = client.get("/transfer-requests")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_transfer_requests() -> None:
    store.create_transfer_request("SKU-1", 1, agent_id="agent-1")
    store.create_transfer_request("SKU-2", 2, agent_id="agent-2")

    resp = client.get("/transfer-requests")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert {r["sku"] for r in body} == {"SKU-1", "SKU-2"}


def test_list_transfer_requests_filters_by_status() -> None:
    store._unavailable_chance = 0.0
    available = store.create_transfer_request("SKU-1", 1)
    store._unavailable_chance = 1.0
    store.create_transfer_request("SKU-2", 1)

    resp = client.get("/transfer-requests", params={"status": "available"})
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [available.id]


def test_list_transfer_requests_rejects_invalid_status() -> None:
    resp = client.get("/transfer-requests", params={"status": "bogus"})
    assert resp.status_code == 400


def test_get_transfer_request() -> None:
    created = store.create_transfer_request("SKU-1", 3, context="short 3 units")

    resp = client.get(f"/transfer-requests/{created.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created.id
    assert body["sku"] == "SKU-1"
    assert body["quantity"] == 3
    assert body["context"] == "short 3 units"


def test_get_transfer_request_not_found() -> None:
    resp = client.get("/transfer-requests/999")
    assert resp.status_code == 404


def test_reset_clears_transfer_requests_too() -> None:
    store.create_transfer_request("SKU-1", 1)
    resp = client.post("/help-requests/reset")
    assert resp.status_code == 200
    assert client.get("/transfer-requests").json() == []
