import json
from dataclasses import dataclass

import pytest

from src import order_extraction as order_extraction_module
from src.order_extraction import ExtractionError, _find_tool_input, extract_order
from src.settings import settings


@dataclass
class _FakeFunctionCall:
    name: str
    arguments: str


@dataclass
class _FakeToolCall:
    id: str
    function: _FakeFunctionCall
    type: str = "function"


class _FakeMessage:
    def __init__(self, tool_calls=None):
        self.tool_calls = tool_calls or []


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]


class _FakeCompletions:
    def __init__(self, response: _FakeResponse | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.call_kwargs: dict | None = None

    def create(self, **kwargs) -> _FakeResponse:
        self.call_kwargs = kwargs
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


class _FakeOpenAIClient:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.chat = type("Chat", (), {"completions": completions})()


def _tool_call(**overrides) -> _FakeToolCall:
    args = {
        "po_number": "PO-1001",
        "line_items": [
            {"sku": "SKU-1", "description": "Widget", "quantity": 2, "unit_price": 10.0},
        ],
        **overrides,
    }
    return _FakeToolCall(id="call_1", function=_FakeFunctionCall(name="record_purchase_order", arguments=json.dumps(args)))


def _install_fake_client(monkeypatch, completions: _FakeCompletions) -> None:
    fake_client = _FakeOpenAIClient(completions)
    monkeypatch.setattr(order_extraction_module, "OpenAI", lambda api_key, base_url=None, timeout=None: fake_client)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")


def test_extract_order_happy_path_parses_line_items(monkeypatch) -> None:
    completions = _FakeCompletions(response=_FakeResponse(_FakeMessage(tool_calls=[_tool_call()])))
    _install_fake_client(monkeypatch, completions)

    order = extract_order("| SKU | Desc |\n| SKU-1 | Widget |")

    assert order.po_number == "PO-1001"
    assert len(order.line_items) == 1
    assert order.line_items[0].sku == "SKU-1"


def test_extract_order_allows_null_sku(monkeypatch) -> None:
    completions = _FakeCompletions(
        response=_FakeResponse(
            _FakeMessage(
                tool_calls=[
                    _tool_call(
                        line_items=[
                            {"sku": None, "description": "Widget", "quantity": 2, "unit_price": 10.0},
                        ]
                    )
                ]
            )
        )
    )
    _install_fake_client(monkeypatch, completions)

    order = extract_order("markdown with no sku column")

    assert order.line_items[0].sku is None


def test_extract_order_uses_deterministic_settings(monkeypatch) -> None:
    completions = _FakeCompletions(response=_FakeResponse(_FakeMessage(tool_calls=[_tool_call()])))
    _install_fake_client(monkeypatch, completions)
    monkeypatch.setattr(settings, "OPENAI_MODEL", "gpt-test")

    extract_order("markdown")

    assert completions.call_kwargs["temperature"] == 0
    assert completions.call_kwargs["model"] == "gpt-test"
    assert completions.call_kwargs["tool_choice"] == {"type": "function", "function": {"name": "record_purchase_order"}}


def test_extract_order_raises_when_api_key_missing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "OPENAI_API_KEY", None)

    with pytest.raises(ExtractionError, match="OPENAI_API_KEY"):
        extract_order("markdown")


def test_extract_order_wraps_openai_call_failure(monkeypatch) -> None:
    completions = _FakeCompletions(error=RuntimeError("boom"))
    _install_fake_client(monkeypatch, completions)

    with pytest.raises(ExtractionError, match="OpenAI API call failed"):
        extract_order("markdown")


def test_extract_order_raises_when_no_tool_call_returned(monkeypatch) -> None:
    completions = _FakeCompletions(response=_FakeResponse(_FakeMessage(tool_calls=[])))
    _install_fake_client(monkeypatch, completions)

    with pytest.raises(ExtractionError, match="did not return a record_purchase_order tool call"):
        extract_order("markdown")


def test_extract_order_raises_on_schema_validation_failure(monkeypatch) -> None:
    completions = _FakeCompletions(
        response=_FakeResponse(_FakeMessage(tool_calls=[_tool_call(po_number=None)]))
    )
    _install_fake_client(monkeypatch, completions)

    with pytest.raises(ExtractionError, match="failed schema validation"):
        extract_order("markdown")


def test_extract_order_raises_when_line_items_missing(monkeypatch) -> None:
    completions = _FakeCompletions(
        response=_FakeResponse(
            _FakeMessage(
                tool_calls=[
                    _FakeToolCall(
                        id="call_1",
                        function=_FakeFunctionCall(
                            name="record_purchase_order", arguments=json.dumps({"po_number": "PO-1001"})
                        ),
                    )
                ]
            )
        )
    )
    _install_fake_client(monkeypatch, completions)

    with pytest.raises(ExtractionError, match="failed schema validation"):
        extract_order("markdown")


def test_find_tool_input_returns_none_when_tool_name_does_not_match() -> None:
    choice = _FakeChoice(
        _FakeMessage(tool_calls=[_FakeToolCall(id="c1", function=_FakeFunctionCall(name="other_tool", arguments="{}"))])
    )

    assert _find_tool_input([choice]) is None


def test_find_tool_input_returns_none_on_malformed_json() -> None:
    choice = _FakeChoice(
        _FakeMessage(
            tool_calls=[
                _FakeToolCall(id="c1", function=_FakeFunctionCall(name="record_purchase_order", arguments="{not json"))
            ]
        )
    )

    assert _find_tool_input([choice]) is None


def test_find_tool_input_returns_none_when_no_tool_calls_attribute() -> None:
    choice = _FakeChoice(_FakeMessage(tool_calls=None))

    assert _find_tool_input([choice]) is None


def test_find_tool_input_finds_match_among_multiple_tool_calls() -> None:
    other = _FakeToolCall(id="c1", function=_FakeFunctionCall(name="unrelated", arguments="{}"))
    match = _tool_call()
    choice = _FakeChoice(_FakeMessage(tool_calls=[other, match]))

    result = _find_tool_input([choice])

    assert result["po_number"] == "PO-1001"
