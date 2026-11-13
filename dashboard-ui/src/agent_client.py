import base64
import logging
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AgentCallError(Exception):
    pass


# A2A 1.0 over JSON-RPC: PascalCase method names, enum values spelled out in
# full (ROLE_USER, TASK_STATE_FAILED), and parts that are told apart by which
# content field they carry (text / data / raw / url) rather than a `kind` tag.
# Requests must declare the protocol version -- a request without this header
# is treated by the agent as coming from a pre-1.0 (v0.3) client.
_A2A_VERSION_HEADERS = {"A2A-Version": "1.0"}
_TASK_STATE_PREFIX = "TASK_STATE_"


def _restore_ints(value: Any) -> Any:
    """Undoes the one lossy step in the agent's data part: it travels as a
    protobuf `Value`, which stores every number as a double, so the order
    record's integer fields (quantities, shipment ids) arrive as 3.0 rather
    than 3. Whole-number floats are turned back into ints; a price like 450.0
    becoming 450 is the same number either way."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _restore_ints(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_restore_ints(item) for item in value]
    return value


def _extract_parts(artifact: dict) -> tuple[dict | None, str | None]:
    data: dict | None = None
    text: str | None = None
    for part in artifact.get("parts", []):
        if "data" in part:
            data = _restore_ints(part["data"])
        elif "text" in part:
            text = part["text"]
    return data, text


def _status_message_text(status: dict) -> str | None:
    message = status.get("message")
    if not message:
        return None
    for part in message.get("parts", []):
        if "text" in part:
            return part["text"]
    return None


async def process_purchase_order(
    agent_url: str,
    pdf_bytes: bytes,
    filename: str,
    progress_webhook: str | None = None,
    timeout: float = 300.0,
    connect_timeout: float = 5.0,
) -> dict[str, Any]:
    """Sends a PDF to a dc-agent's A2A `SendMessage` endpoint and waits for the
    (blocking) result. If `progress_webhook` is set, the agent will POST an event
    to it for every processing stage and tool call - see local-dc-agent's
    agent_executor.py:_build_progress_hook.

    `connect_timeout` is kept short (unlike the overall `timeout`, which has to
    accommodate slow LLM processing) so an unreachable agent fails fast instead
    of hanging the UI for the full request timeout."""
    message: dict[str, Any] = {
        "role": "ROLE_USER",
        "messageId": str(uuid.uuid4()),
        "parts": [
            {
                "raw": base64.b64encode(pdf_bytes).decode("ascii"),
                "mediaType": "application/pdf",
                "filename": filename,
            }
        ],
    }
    if progress_webhook:
        message["metadata"] = {"progress_webhook": progress_webhook}

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {"message": message},
    }

    request_timeout = httpx.Timeout(timeout, connect=connect_timeout)
    logger.info("Sending %s to agent at %s", filename, agent_url)
    try:
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            response = await client.post(
                agent_url.rstrip("/") + "/", json=payload, headers=_A2A_VERSION_HEADERS
            )
    except httpx.ConnectError as exc:
        logger.warning("Could not reach agent at %s: %s", agent_url, exc)
        raise AgentCallError(f"Could not reach agent at {agent_url}: {exc}") from exc
    except httpx.TimeoutException as exc:
        logger.warning("Agent at %s timed out: %s", agent_url, exc)
        raise AgentCallError(f"Agent at {agent_url} timed out: {exc}") from exc
    except httpx.HTTPError as exc:
        logger.warning("Error communicating with agent at %s: %s", agent_url, exc)
        raise AgentCallError(f"Error communicating with agent at {agent_url}: {exc}") from exc

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning("Agent at %s returned %s", agent_url, response.status_code)
        raise AgentCallError(
            f"Agent at {agent_url} returned {response.status_code}: {response.text[:500]}"
        ) from exc
    body = response.json()

    if "error" in body:
        logger.warning("Agent at %s returned a JSON-RPC error: %s", agent_url, body["error"])
        raise AgentCallError(body["error"].get("message", "agent returned a JSON-RPC error"))

    # SendMessage answers with either a `task` or a bare `message`; this agent
    # always creates a task, so anything else has no order data to return.
    task = (body.get("result") or {}).get("task") or {}
    status = task.get("status") or {}
    # Reported to callers without the enum prefix ("completed", "failed").
    state = (status.get("state") or "").removeprefix(_TASK_STATE_PREFIX).lower() or None

    if state == "failed":
        message = _status_message_text(status) or "agent task failed"
        logger.warning("Agent task for %s failed: %s", filename, message)
        raise AgentCallError(message)

    data, summary = None, None
    for artifact in task.get("artifacts") or []:
        artifact_data, artifact_text = _extract_parts(artifact)
        data = data or artifact_data
        summary = summary or artifact_text

    if data is None:
        raise AgentCallError("agent response had no processed-order data artifact")

    logger.info("Agent task for %s completed: state=%s", filename, state)
    return {"state": state, "result": data, "summary": summary}
