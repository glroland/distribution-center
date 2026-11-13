"""A2A client for local-dc-agent, adapted from dashboard-ui/src/agent_client.py
(same `SendMessage` envelope, same progress_webhook metadata convention) --
eval-suite drives the agent the same way the real UI does, rather than
reaching into its internals, so the mcp-trajectory and end-to-end benchmarks
stay genuinely black-box."""

import base64
import logging
import uuid
from pathlib import Path
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
    pdf_path: str | Path,
    progress_webhook: str | None = None,
    timeout: float = 300.0,
    connect_timeout: float = 5.0,
) -> dict[str, Any]:
    """Sends a PO PDF to a dc-agent's A2A `SendMessage` endpoint and waits for the
    (blocking) result, matching dashboard-ui's client. Returns
    {"state", "result": <ProcessOrderResult dict>, "summary"}."""
    pdf_bytes = Path(pdf_path).read_bytes()
    filename = Path(pdf_path).name

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
        raise AgentCallError(f"Could not reach agent at {agent_url}: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise AgentCallError(f"Agent at {agent_url} timed out: {exc}") from exc
    except httpx.HTTPError as exc:
        raise AgentCallError(f"Error communicating with agent at {agent_url}: {exc}") from exc

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise AgentCallError(
            f"Agent at {agent_url} returned {response.status_code}: {response.text[:500]}"
        ) from exc
    body = response.json()

    if "error" in body:
        raise AgentCallError(body["error"].get("message", "agent returned a JSON-RPC error"))

    # SendMessage answers with either a `task` or a bare `message`; this agent
    # always creates a task, so anything else has no order data to return.
    task = (body.get("result") or {}).get("task") or {}
    status = task.get("status") or {}
    # Reported to callers without the enum prefix ("completed", "failed").
    state = (status.get("state") or "").removeprefix(_TASK_STATE_PREFIX).lower() or None

    if state == "failed":
        raise AgentCallError(_status_message_text(status) or "agent task failed")

    data, summary = None, None
    for artifact in task.get("artifacts") or []:
        artifact_data, artifact_text = _extract_parts(artifact)
        data = data or artifact_data
        summary = summary or artifact_text

    if data is None:
        raise AgentCallError("agent response had no processed-order data artifact")

    logger.info("Agent task for %s completed: state=%s", filename, state)
    return {"state": state, "result": data, "summary": summary}
