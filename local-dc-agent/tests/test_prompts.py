import json

from mlflow.entities.model_registry import PromptVersion

from src import prompts as prompts_module
from src.prompts import get_prompt
from src.settings import settings


class _FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict = {}

    def set_attribute(self, key, value) -> None:
        self.attributes[key] = value


class _FakeTraceManager:
    def __init__(self) -> None:
        self.registered: list[tuple[str, PromptVersion]] = []

    def register_prompt(self, trace_id, prompt) -> None:
        self.registered.append((trace_id, prompt))


def _fake_prompt_version(name: str, version: int = 4) -> PromptVersion:
    return PromptVersion(name=name, version=version, template="hello {{who}}")


def test_get_prompt_local_mode_never_touches_trace_linking(monkeypatch) -> None:
    """PROMPT_SOURCE=local has no registry version to link, so linking must be
    skipped outright -- touching InMemoryTraceManager here would be a bug."""
    monkeypatch.setattr(settings, "PROMPT_SOURCE", "local")

    def _boom():
        raise AssertionError("trace linking must not run for PROMPT_SOURCE=local")

    monkeypatch.setattr(prompts_module.InMemoryTraceManager, "get_instance", _boom)

    prompt = get_prompt("dc-agent.order_extraction.system_prompt")
    assert prompt.template


def test_get_prompt_mlflow_mode_links_on_first_load(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PROMPT_SOURCE", "mlflow")
    fake_prompt = _fake_prompt_version("dc-agent.order_extraction.system_prompt")
    monkeypatch.setattr(prompts_module.mlflow.genai, "load_prompt", lambda prompt_id: fake_prompt)

    span = _FakeSpan()
    manager = _FakeTraceManager()
    monkeypatch.setattr(prompts_module.mlflow, "get_active_trace_id", lambda: "tr-123")
    monkeypatch.setattr(prompts_module.mlflow, "get_current_active_span", lambda: span)
    monkeypatch.setattr(prompts_module.InMemoryTraceManager, "get_instance", lambda: manager)

    get_prompt("dc-agent.order_extraction.system_prompt")

    assert manager.registered == [("tr-123", fake_prompt)]
    linked = json.loads(span.attributes["mlflow.linkedPrompts"])
    assert linked == [{"name": fake_prompt.name, "version": "4"}]


def test_get_prompt_mlflow_mode_links_again_on_cache_hit(monkeypatch) -> None:
    """The bug this fixes: get_prompt() only calls mlflow.genai.load_prompt()
    (which auto-links) on the first call per process -- every later call in
    the *same* process re-uses the cached PromptVersion and must still link
    the *new* active trace, or every trace after the first ships unlinked."""
    monkeypatch.setattr(settings, "PROMPT_SOURCE", "mlflow")
    fake_prompt = _fake_prompt_version("dc-agent.order_extraction.system_prompt")
    monkeypatch.setattr(prompts_module.mlflow.genai, "load_prompt", lambda prompt_id: fake_prompt)

    manager = _FakeTraceManager()
    monkeypatch.setattr(prompts_module.InMemoryTraceManager, "get_instance", lambda: manager)

    # First call: process startup, trace "tr-first".
    monkeypatch.setattr(prompts_module.mlflow, "get_active_trace_id", lambda: "tr-first")
    monkeypatch.setattr(prompts_module.mlflow, "get_current_active_span", lambda: _FakeSpan())
    get_prompt("dc-agent.order_extraction.system_prompt")

    # Second call: a later PO, cache hit, a brand new trace "tr-second".
    span_two = _FakeSpan()
    monkeypatch.setattr(prompts_module.mlflow, "get_active_trace_id", lambda: "tr-second")
    monkeypatch.setattr(prompts_module.mlflow, "get_current_active_span", lambda: span_two)
    get_prompt("dc-agent.order_extraction.system_prompt")

    assert [trace_id for trace_id, _ in manager.registered] == ["tr-first", "tr-second"]
    linked = json.loads(span_two.attributes["mlflow.linkedPrompts"])
    assert linked == [{"name": fake_prompt.name, "version": "4"}]


def test_get_prompt_mlflow_mode_noop_without_active_trace_or_span(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PROMPT_SOURCE", "mlflow")
    fake_prompt = _fake_prompt_version("dc-agent.order_extraction.system_prompt")
    monkeypatch.setattr(prompts_module.mlflow.genai, "load_prompt", lambda prompt_id: fake_prompt)

    manager = _FakeTraceManager()
    monkeypatch.setattr(prompts_module.InMemoryTraceManager, "get_instance", lambda: manager)
    monkeypatch.setattr(prompts_module.mlflow, "get_active_trace_id", lambda: None)
    monkeypatch.setattr(prompts_module.mlflow, "get_current_active_span", lambda: None)

    # Should not raise even though there's nothing to link against.
    get_prompt("dc-agent.order_extraction.system_prompt")
    assert manager.registered == []


def test_get_prompt_mlflow_mode_linking_failure_does_not_raise(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PROMPT_SOURCE", "mlflow")
    fake_prompt = _fake_prompt_version("dc-agent.order_extraction.system_prompt")
    monkeypatch.setattr(prompts_module.mlflow.genai, "load_prompt", lambda prompt_id: fake_prompt)

    def _boom():
        raise RuntimeError("registry unreachable")

    monkeypatch.setattr(prompts_module.mlflow, "get_active_trace_id", _boom)

    prompt = get_prompt("dc-agent.order_extraction.system_prompt")
    assert prompt is fake_prompt
