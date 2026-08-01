import pytest

from src.store import HelpRequestAlreadyResolvedError, HelpRequestNotFoundError, SupervisorStore


def _store() -> SupervisorStore:
    return SupervisorStore()


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
