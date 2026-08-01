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

from src.agent_executor import ProcessOrderAgentExecutor
from src.fulfillment import FulfillmentError
from src.ingest_client import IngestError
from src.models import ExtractedOrder, LineItem, ProcessOrderResult
from src.order_extraction import ExtractionError
from src.order_processing import process_order


class _FakeWorker:
    def __init__(self, result: ProcessOrderResult | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple[bytes, str]] = []

    async def submit(self, pdf_bytes: bytes, filename: str) -> ProcessOrderResult:
        self.calls.append((pdf_bytes, filename))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


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


def _sample_result() -> ProcessOrderResult:
    extracted = ExtractedOrder(
        po_number="PO-2002",
        vendor_name="Northwind Traders",
        line_items=[LineItem(sku="SKU-9", description="Pallet Jack", quantity=1, unit_price=450.0)],
        stated_total=450.0,
    )
    return process_order(extracted)


@pytest.mark.asyncio
async def test_process_purchase_order_success() -> None:
    worker = _FakeWorker(result=_sample_result())
    context = RequestContext(request=MessageSendParams(message=_message([_pdf_part()])))
    queue = EventQueue()

    await ProcessOrderAgentExecutor(worker).execute(context, queue)

    events = await _drain(queue)
    artifact_events = [e for e in events if hasattr(e, "artifact")]
    status_events = [e for e in events if hasattr(e, "status")]

    assert len(artifact_events) == 1
    data_parts = [p.root.data for p in artifact_events[0].artifact.parts if isinstance(p.root, DataPart)]
    text_parts = [p.root.text for p in artifact_events[0].artifact.parts if isinstance(p.root, TextPart)]
    assert data_parts[0]["po_number"] == "PO-2002"
    assert "PO-2002" in text_parts[0]

    assert status_events[-1].status.state == TaskState.completed
    assert len(worker.calls) == 1


@pytest.mark.asyncio
async def test_missing_pdf_part_fails_task() -> None:
    worker = _FakeWorker()
    context = RequestContext(request=MessageSendParams(message=_message([Part(root=TextPart(text="hello"))])))
    queue = EventQueue()

    await ProcessOrderAgentExecutor(worker).execute(context, queue)

    events = await _drain(queue)
    status_events = [e for e in events if hasattr(e, "status")]
    assert status_events[-1].status.state == TaskState.failed
    assert worker.calls == []


@pytest.mark.asyncio
async def test_ingest_failure_fails_task() -> None:
    worker = _FakeWorker(error=IngestError("docling exploded"))
    context = RequestContext(request=MessageSendParams(message=_message([_pdf_part()])))
    queue = EventQueue()

    await ProcessOrderAgentExecutor(worker).execute(context, queue)

    events = await _drain(queue)
    status_events = [e for e in events if hasattr(e, "status")]
    assert status_events[-1].status.state == TaskState.failed


@pytest.mark.asyncio
async def test_extraction_failure_fails_task() -> None:
    worker = _FakeWorker(error=ExtractionError("model refused"))
    context = RequestContext(request=MessageSendParams(message=_message([_pdf_part()])))
    queue = EventQueue()

    await ProcessOrderAgentExecutor(worker).execute(context, queue)

    events = await _drain(queue)
    status_events = [e for e in events if hasattr(e, "status")]
    assert status_events[-1].status.state == TaskState.failed


@pytest.mark.asyncio
async def test_fulfillment_failure_fails_task() -> None:
    worker = _FakeWorker(error=FulfillmentError("OpenAI API call failed"))
    context = RequestContext(request=MessageSendParams(message=_message([_pdf_part()])))
    queue = EventQueue()

    await ProcessOrderAgentExecutor(worker).execute(context, queue)

    events = await _drain(queue)
    status_events = [e for e in events if hasattr(e, "status")]
    assert status_events[-1].status.state == TaskState.failed
