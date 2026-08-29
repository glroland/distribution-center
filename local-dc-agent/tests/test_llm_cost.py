from dataclasses import dataclass

from src import llm_cost as llm_cost_module
from src.llm_cost import cost_from_totals, record_usage_and_cost, usage_totals
from src.settings import settings


@dataclass
class _FakeUsage:
    prompt_tokens: int
    completion_tokens: int


class _FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict = {}

    def set_attribute(self, key, value) -> None:
        self.attributes[key] = value


def test_usage_totals_sums_across_multiple_usages() -> None:
    totals = usage_totals(_FakeUsage(10, 5), _FakeUsage(20, 1))
    assert totals == {"input_tokens": 30, "output_tokens": 6, "total_tokens": 36}


def test_usage_totals_ignores_none() -> None:
    totals = usage_totals(_FakeUsage(10, 5), None)
    assert totals == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def test_cost_from_totals_computes_dollars_per_million_tokens() -> None:
    totals = {"input_tokens": 1_000_000, "output_tokens": 500_000, "total_tokens": 1_500_000}
    cost = cost_from_totals(totals, cost_per_million_tokens=2.0)
    assert cost == {"input_cost": 2.0, "output_cost": 1.0, "total_cost": 3.0}


def test_cost_from_totals_zero_rate_is_zero_cost() -> None:
    totals = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "total_tokens": 2_000_000}
    assert cost_from_totals(totals, cost_per_million_tokens=0.0) == {
        "input_cost": 0.0,
        "output_cost": 0.0,
        "total_cost": 0.0,
    }


def test_record_usage_and_cost_sets_attributes_on_current_span(monkeypatch) -> None:
    span = _FakeSpan()
    monkeypatch.setattr(llm_cost_module.mlflow, "get_current_active_span", lambda: span)
    monkeypatch.setattr(settings, "LLM_COST_PER_MILLION_TOKENS", 4.0)

    record_usage_and_cost(_FakeUsage(1_000_000, 500_000))

    assert span.attributes["mlflow.chat.tokenUsage"] == {
        "input_tokens": 1_000_000,
        "output_tokens": 500_000,
        "total_tokens": 1_500_000,
    }
    assert span.attributes["mlflow.llm.cost"] == {
        "input_cost": 4.0,
        "output_cost": 2.0,
        "total_cost": 6.0,
    }


def test_record_usage_and_cost_accumulates_across_calls(monkeypatch) -> None:
    """fulfillment.py calls this once per turn with all usages seen so far --
    each call must overwrite with the full cumulative total, not just the
    latest turn's numbers."""
    span = _FakeSpan()
    monkeypatch.setattr(llm_cost_module.mlflow, "get_current_active_span", lambda: span)
    monkeypatch.setattr(settings, "LLM_COST_PER_MILLION_TOKENS", 1.0)

    record_usage_and_cost(_FakeUsage(100, 50))
    record_usage_and_cost(_FakeUsage(100, 50), _FakeUsage(200, 25))

    assert span.attributes["mlflow.chat.tokenUsage"] == {
        "input_tokens": 300,
        "output_tokens": 75,
        "total_tokens": 375,
    }


def test_record_usage_and_cost_noop_without_active_span(monkeypatch) -> None:
    monkeypatch.setattr(llm_cost_module.mlflow, "get_current_active_span", lambda: None)
    # Should not raise even though there's nothing to attach attributes to.
    record_usage_and_cost(_FakeUsage(10, 5))


def test_record_usage_and_cost_noop_when_no_usage(monkeypatch) -> None:
    span = _FakeSpan()
    monkeypatch.setattr(llm_cost_module.mlflow, "get_current_active_span", lambda: span)

    record_usage_and_cost(None)

    assert span.attributes == {}
