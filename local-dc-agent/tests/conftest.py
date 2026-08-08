import pytest

from src import guardrails as guardrails_module
from src import prompts as prompts_module


@pytest.fixture(autouse=True)
def _reset_prompt_cache():
    prompts_module.reset_prompt_cache()
    yield
    prompts_module.reset_prompt_cache()


@pytest.fixture(autouse=True)
def _reset_agentic_safety():
    """guardrails.is_enabled()'s state is a module-level global (the
    "Agentic Safety" toggle) -- reset it around every test so one test
    flipping it off can't leak into an unrelated test that assumes the
    default-on behavior."""
    guardrails_module.set_enabled(True)
    yield
    guardrails_module.set_enabled(True)
