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
