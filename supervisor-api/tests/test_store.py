import pytest

from src.store import (
    HelpRequestAlreadyResolvedError,
    HelpRequestNotFoundError,
    SupervisorStore,
    TransferRequestNotFoundError,
)


def _store(unavailable_chance: float = 1 / 3) -> SupervisorStore:
    return SupervisorStore(unavailable_chance=unavailable_chance)


def test_create_help_request() -> None:
    store = _store()
    request = store.create_help_request("What do I do?", agent_id="agent-1", context="stuck")
    assert request.id == 1
    assert request.agent_id == "agent-1"
    assert request.question == "What do I do?"
    assert request.context == "stuck"
    assert request.status == "open"
    assert request.resolved_at is None
    assert request.resolution is None


def test_create_help_request_assigns_incrementing_ids() -> None:
    store = _store()
    first = store.create_help_request("first?")
    second = store.create_help_request("second?")
    assert (first.id, second.id) == (1, 2)


def test_create_help_request_blank_question_raises() -> None:
    store = _store()
    with pytest.raises(ValueError):
        store.create_help_request("   ")


def test_list_help_requests_returns_all() -> None:
    store = _store()
    store.create_help_request("first?")
    store.create_help_request("second?")
    assert len(store.list_help_requests()) == 2


def test_list_help_requests_filters_by_status() -> None:
    store = _store()
    open_request = store.create_help_request("first?")
    resolved_request = store.create_help_request("second?")
    store.resolve_help_request(resolved_request.id, "fixed it")

    open_only = store.list_help_requests(status="open")
    resolved_only = store.list_help_requests(status="resolved")
    assert [r.id for r in open_only] == [open_request.id]
    assert [r.id for r in resolved_only] == [resolved_request.id]


def test_get_help_request() -> None:
    store = _store()
    created = store.create_help_request("first?")
    assert store.get_help_request(created.id) is created


def test_get_help_request_unknown_id_raises() -> None:
    store = _store()
    with pytest.raises(HelpRequestNotFoundError):
        store.get_help_request(999)


def test_resolve_help_request() -> None:
    store = _store()
    created = store.create_help_request("first?")
    resolved = store.resolve_help_request(created.id, "here's the answer")
    assert resolved.status == "resolved"
    assert resolved.resolution == "here's the answer"
    assert resolved.resolved_at is not None


def test_resolve_help_request_unknown_id_raises() -> None:
    store = _store()
    with pytest.raises(HelpRequestNotFoundError):
        store.resolve_help_request(999, "answer")


def test_resolve_help_request_already_resolved_raises() -> None:
    store = _store()
    created = store.create_help_request("first?")
    store.resolve_help_request(created.id, "first answer")
    with pytest.raises(HelpRequestAlreadyResolvedError):
        store.resolve_help_request(created.id, "second answer")


def test_reset_clears_requests_and_id_counter() -> None:
    store = _store()
    store.create_help_request("first?")
    store.reset()
    assert store.list_help_requests() == []
    fresh = store.create_help_request("after reset?")
    assert fresh.id == 1


def test_create_transfer_request_always_available() -> None:
    store = _store(unavailable_chance=0.0)
    request = store.create_transfer_request("SKU-1", 5, agent_id="agent-1", context="short 5 units")
    assert request.id == 1
    assert request.sku == "SKU-1"
    assert request.quantity == 5
    assert request.agent_id == "agent-1"
    assert request.context == "short 5 units"
    assert request.status == "available"
    assert request.source_location is not None


def test_create_transfer_request_always_unavailable() -> None:
    store = _store(unavailable_chance=1.0)
    request = store.create_transfer_request("SKU-1", 5)
    assert request.status == "unavailable"
    assert request.source_location is None


def test_create_transfer_request_assigns_incrementing_ids() -> None:
    store = _store()
    first = store.create_transfer_request("SKU-1", 1)
    second = store.create_transfer_request("SKU-2", 1)
    assert (first.id, second.id) == (1, 2)


def test_create_transfer_request_blank_sku_raises() -> None:
    store = _store()
    with pytest.raises(ValueError):
        store.create_transfer_request("   ", 1)


def test_create_transfer_request_nonpositive_quantity_raises() -> None:
    store = _store()
    with pytest.raises(ValueError):
        store.create_transfer_request("SKU-1", 0)


def test_list_transfer_requests_filters_by_status() -> None:
    store = _store(unavailable_chance=0.0)
    available = store.create_transfer_request("SKU-1", 1)
    store2 = _store(unavailable_chance=1.0)
    unavailable = store2.create_transfer_request("SKU-2", 1)

    assert [r.id for r in store.list_transfer_requests(status="available")] == [available.id]
    assert [r.id for r in store2.list_transfer_requests(status="unavailable")] == [unavailable.id]


def test_get_transfer_request() -> None:
    store = _store()
    created = store.create_transfer_request("SKU-1", 1)
    assert store.get_transfer_request(created.id) is created


def test_get_transfer_request_unknown_id_raises() -> None:
    store = _store()
    with pytest.raises(TransferRequestNotFoundError):
        store.get_transfer_request(999)


def test_reset_clears_transfer_requests_and_id_counter() -> None:
    store = _store()
    store.create_transfer_request("SKU-1", 1)
    store.reset()
    assert store.list_transfer_requests() == []
    fresh = store.create_transfer_request("SKU-2", 1)
    assert fresh.id == 1
