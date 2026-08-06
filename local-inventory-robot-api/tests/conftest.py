import pytest

from src import prompts as prompts_module


@pytest.fixture(autouse=True)
def _reset_prompt_cache():
    prompts_module.reset_prompt_cache()
    yield
    prompts_module.reset_prompt_cache()
