"""Directly exercises the /guardrails route handlers (the "Agentic Safety"
toggle's REST surface) as plain functions, the same way this repo's other
handler-level logic is tested -- no other test in this suite spins up the
full A2A/Starlette app (that would also kick off OrderWorker's background
MCP-connect loop against unreachable localhost URLs), so this stays
consistent with that rather than being the first to do so.
"""

import pytest

from src import guardrails
from src.app import get_guardrails, set_guardrails


class _FakeRequest:
    def __init__(self, body: dict) -> None:
        self._body = body

    async def json(self) -> dict:
        return self._body


@pytest.mark.asyncio
async def test_get_guardrails_reflects_current_state() -> None:
    guardrails.set_enabled(True)
    response = await get_guardrails(_FakeRequest({}))
    assert response.status_code == 200
    assert response.body == b'{"enabled":true}'

    guardrails.set_enabled(False)
    response = await get_guardrails(_FakeRequest({}))
    assert response.body == b'{"enabled":false}'


@pytest.mark.asyncio
async def test_set_guardrails_updates_state_and_echoes_it() -> None:
    response = await set_guardrails(_FakeRequest({"enabled": False}))

    assert response.body == b'{"enabled":false}'
    assert guardrails.is_enabled() is False

    response = await set_guardrails(_FakeRequest({"enabled": True}))

    assert response.body == b'{"enabled":true}'
    assert guardrails.is_enabled() is True
