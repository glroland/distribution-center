import asyncio
import base64

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    DataPart,
    FilePart,
    FileWithBytes,
    Message,
    MessageSendParams,
    Part,
    Role,
    TaskState,
    TextPart,
)

from src import agent_executor as executor_module
from src.agent_executor import ProcessOrderAgentExecutor
from src.ingest_client import IngestError
from src.models import ExtractedOrder, LineItem
from src.order_extraction import ExtractionError


def _message(parts: list[Part]) -> Message:
    return Message(message_id="m1", role=Role.user, parts=parts, kind="message")


def _pdf_part(name: str = "po.pdf") -> Part:
    file = FileWithBytes(bytes=base64.b64encode(b"%PDF-1.4 fake").decode(), mime_type="application/pdf", name=name)
    return Part(root=FilePart(file=file))


async def _drain(queue: EventQueue) -> list:
    events = []
    while True:
        try:
            events.append(await queue.dequeue_event(no_wait=True))
        except asyncio.QueueEmpty:
            break
    return events


def _sample_extracted_order() -> ExtractedOrder:
    return ExtractedOrder(
        po_number="PO-2002",
        vendor_name="Northwind Traders",
        line_items=[LineItem(sku="SKU-9", description="Pallet Jack", quantity=1, unit_price=450.0)],
        stated_total=450.0,
    )


@pytest.mark.asyncio
async def test_process_purchase_order_success(monkeypatch) -> None:
    async def fake_convert(pdf_bytes: bytes, filename: str) -> str:
        return "# Purchase Order PO-2002"

    def fake_extract(markdown: str) -> ExtractedOrder:
        return _sample_extracted_order()

    monkeypatch.setattr(executor_module, "convert_pdf_to_markdown", fake_convert)
    monkeypatch.setattr(executor_module, "extract_order", fake_extract)

    context = RequestContext(request=MessageSendParams(message=_message([_pdf_part()])))
    queue = EventQueue()

    await ProcessOrderAgentExecutor().execute(context, queue)

    events = await _drain(queue)
    artifact_events = [e for e in events if hasattr(e, "artifact")]
    status_events = [e for e in events if hasattr(e, "status")]

    assert len(artifact_events) == 1
    data_parts = [p.root.data for p in artifact_events[0].artifact.parts if isinstance(p.root, DataPart)]
    text_parts = [p.root.text for p in artifact_events[0].artifact.parts if isinstance(p.root, TextPart)]
    assert data_parts[0]["po_number"] == "PO-2002"
    assert "PO-2002" in text_parts[0]

    assert status_events[-1].status.state == TaskState.completed


@pytest.mark.asyncio
async def test_missing_pdf_part_fails_task() -> None:
    context = RequestContext(request=MessageSendParams(message=_message([Part(root=TextPart(text="hello"))])))
    queue = EventQueue()

    await ProcessOrderAgentExecutor().execute(context, queue)

    events = await _drain(queue)
    status_events = [e for e in events if hasattr(e, "status")]
    assert status_events[-1].status.state == TaskState.failed


@pytest.mark.asyncio
async def test_ingest_failure_fails_task(monkeypatch) -> None:
    async def fake_convert(pdf_bytes: bytes, filename: str) -> str:
        raise IngestError("docling exploded")

    monkeypatch.setattr(executor_module, "convert_pdf_to_markdown", fake_convert)

    context = RequestContext(request=MessageSendParams(message=_message([_pdf_part()])))
    queue = EventQueue()

    await ProcessOrderAgentExecutor().execute(context, queue)

    events = await _drain(queue)
    status_events = [e for e in events if hasattr(e, "status")]
    assert status_events[-1].status.state == TaskState.failed


@pytest.mark.asyncio
async def test_extraction_failure_fails_task(monkeypatch) -> None:
    async def fake_convert(pdf_bytes: bytes, filename: str) -> str:
        return "# Purchase Order"

    def fake_extract(markdown: str) -> ExtractedOrder:
        raise ExtractionError("model refused")

    monkeypatch.setattr(executor_module, "convert_pdf_to_markdown", fake_convert)
    monkeypatch.setattr(executor_module, "extract_order", fake_extract)

    context = RequestContext(request=MessageSendParams(message=_message([_pdf_part()])))
    queue = EventQueue()

    await ProcessOrderAgentExecutor().execute(context, queue)

    events = await _drain(queue)
    status_events = [e for e in events if hasattr(e, "status")]
    assert status_events[-1].status.state == TaskState.failed
