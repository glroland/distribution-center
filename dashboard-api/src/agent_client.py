import base64
import uuid
from typing import Any

import httpx


class AgentCallError(Exception):
    pass


def _extract_parts(artifact: dict) -> tuple[dict | None, str | None]:
    data: dict | None = None
    text: str | None = None
    for part in artifact.get("parts", []):
        # a2a-sdk's Part is a `root`-wrapped union in Python, but the wire JSON is the
        # flattened member itself - handle both just in case a client nests it.
        node = part.get("root", part)
        kind = node.get("kind")
        if kind == "data":
            data = node.get("data")
        elif kind == "text":
            text = node.get("text")
    return data, text


def _status_message_text(status: dict) -> str | None:
    message = status.get("message")
    if not message:
        return None
    for part in message.get("parts", []):
        node = part.get("root", part)
        if node.get("kind") == "text":
            return node.get("text")
    return None


async def process_purchase_order(
    agent_url: str,
    pdf_bytes: bytes,
    filename: str,
    progress_webhook: str | None = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Sends a PDF to a dc-agent's A2A `message/send` endpoint and waits for the
    (blocking) result. If `progress_webhook` is set, the agent will POST an event
    to it for every processing stage and tool call - see local-dc-agent's
    agent_executor.py:_build_progress_hook."""
    message: dict[str, Any] = {
        "role": "user",
        "messageId": str(uuid.uuid4()),
        "kind": "message",
        "parts": [
            {
                "kind": "file",
                "file": {
                    "bytes": base64.b64encode(pdf_bytes).decode("ascii"),
                    "mimeType": "application/pdf",
                    "name": filename,
                },
            }
        ],
    }
    if progress_webhook:
        message["metadata"] = {"progress_webhook": progress_webhook}

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {"message": message},
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(agent_url.rstrip("/") + "/", json=payload)
    response.raise_for_status()
    body = response.json()

    if "error" in body:
        raise AgentCallError(body["error"].get("message", "agent returned a JSON-RPC error"))

    result = body.get("result") or {}
    status = result.get("status") or {}
    state = status.get("state")

    if state == "failed":
        raise AgentCallError(_status_message_text(status) or "agent task failed")

    data, summary = None, None
    for artifact in result.get("artifacts") or []:
        artifact_data, artifact_text = _extract_parts(artifact)
        data = data or artifact_data
        summary = summary or artifact_text

    if data is None:
        raise AgentCallError("agent response had no processed-order data artifact")

    return {"state": state, "result": data, "summary": summary}
