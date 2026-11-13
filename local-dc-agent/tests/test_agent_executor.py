import pytest
from a2a.helpers import get_data_parts, get_text_parts, new_raw_part, new_text_part
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Message, Part, Role, SendMessageRequest, TaskState
from a2a.utils.errors import UnsupportedOperationError

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
        self.on_events: list = []

    async def submit(self, pdf_bytes: bytes, filename: str, on_event=None) -> ProcessOrderResult:
        self.calls.append((pdf_bytes, filename))
        self.on_events.append(on_event)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _message(parts: list[Part], metadata: dict | None = None) -> Message:
    return Message(message_id="m1", role=Role.ROLE_USER, parts=parts, metadata=metadata)


def _pdf_part(name: str = "po.pdf") -> Part:
    return new_raw_part(b"%PDF-1.4 fake", media_type="application/pdf", filename=name)


def _context(message: Message) -> RequestContext:
    return RequestContext(call_context=ServerCallContext(), request=SendMessageRequest(message=message))


class _RecordingQueue:
    """Stands in for the SDK's EventQueue: the executor only ever enqueues."""

    def __init__(self) -> None:
        self.events: list = []

    async def enqueue_event(self, event) -> None:
        self.events.append(event)

    @property
    def artifact_events(self) -> list:
        return [e for e in self.events if hasattr(e, "artifact")]

    @property
    def status_events(self) -> list:
        # the initial Task also has a .status; only the update events count here
        return [e for e in self.events if hasattr(e, "status") and not hasattr(e, "history")]


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
    context = _context(_message([_pdf_part()]))
    queue = _RecordingQueue()

    await ProcessOrderAgentExecutor(worker).execute(context, queue)

    assert len(queue.artifact_events) == 1
    artifact = queue.artifact_events[0].artifact
    assert artifact.name == "processed_order"
    data_parts = get_data_parts(artifact.parts)
    text_parts = get_text_parts(artifact.parts)
    assert data_parts[0]["po_number"] == "PO-2002"
    assert data_parts[0]["line_items"][0]["quantity"] == 1
    assert "PO-2002" in text_parts[0]

    assert queue.status_events[-1].status.state == TaskState.TASK_STATE_COMPLETED
    assert worker.calls == [(b"%PDF-1.4 fake", "po.pdf")]


@pytest.mark.asyncio
async def test_no_progress_webhook_means_no_on_event_hook() -> None:
    worker = _FakeWorker(result=_sample_result())
    context = _context(_message([_pdf_part()]))
    queue = _RecordingQueue()

    await ProcessOrderAgentExecutor(worker).execute(context, queue)

    assert worker.on_events == [None]


@pytest.mark.asyncio
async def test_progress_webhook_metadata_posts_each_event(monkeypatch) -> None:
    posted: list[tuple[str, dict]] = []

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class _FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *exc_info) -> None:
            return None

        async def post(self, url: str, json: dict) -> _FakeResponse:
            posted.append((url, json))
            return _FakeResponse()

    import src.agent_executor as agent_executor_module

    monkeypatch.setattr(agent_executor_module.httpx, "AsyncClient", _FakeAsyncClient)

    worker = _FakeWorker(result=_sample_result())
    message = _message([_pdf_part()], metadata={"progress_webhook": "http://dashboard.local/events/run-1"})
    context = _context(message)
    queue = _RecordingQueue()

    await ProcessOrderAgentExecutor(worker).execute(context, queue)

    (on_event,) = worker.on_events
    assert on_event is not None
    await on_event("tool_call", {"name": "wms__get_inventory_status"})

    assert posted == [
        ("http://dashboard.local/events/run-1", {"type": "tool_call", "data": {"name": "wms__get_inventory_status"}})
    ]


@pytest.mark.asyncio
async def test_missing_pdf_part_fails_task() -> None:
    worker = _FakeWorker()
    context = _context(_message([new_text_part("hello")]))
    queue = _RecordingQueue()

    await ProcessOrderAgentExecutor(worker).execute(context, queue)

    failure = queue.status_events[-1].status
    assert failure.state == TaskState.TASK_STATE_FAILED
    assert "No PDF file part" in get_text_parts(failure.message.parts)[0]
    assert worker.calls == []


@pytest.mark.asyncio
async def test_ingest_failure_fails_task() -> None:
    worker = _FakeWorker(error=IngestError("docling exploded"))
    context = _context(_message([_pdf_part()]))
    queue = _RecordingQueue()

    await ProcessOrderAgentExecutor(worker).execute(context, queue)

    assert queue.status_events[-1].status.state == TaskState.TASK_STATE_FAILED


@pytest.mark.asyncio
async def test_extraction_failure_fails_task() -> None:
    worker = _FakeWorker(error=ExtractionError("model refused"))
    context = _context(_message([_pdf_part()]))
    queue = _RecordingQueue()

    await ProcessOrderAgentExecutor(worker).execute(context, queue)

    assert queue.status_events[-1].status.state == TaskState.TASK_STATE_FAILED


@pytest.mark.asyncio
async def test_fulfillment_failure_fails_task() -> None:
    worker = _FakeWorker(error=FulfillmentError("OpenAI API call failed"))
    context = _context(_message([_pdf_part()]))
    queue = _RecordingQueue()

    await ProcessOrderAgentExecutor(worker).execute(context, queue)

    assert queue.status_events[-1].status.state == TaskState.TASK_STATE_FAILED


@pytest.mark.asyncio
async def test_pdf_referenced_by_url_is_downloaded(monkeypatch) -> None:
    fetched: list[str] = []

    class _FakeResponse:
        content = b"%PDF-1.4 remote"

        def raise_for_status(self) -> None:
            return None

    class _FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *exc_info) -> None:
            return None

        async def get(self, url: str) -> _FakeResponse:
            fetched.append(url)
            return _FakeResponse()

    import src.agent_executor as agent_executor_module

    monkeypatch.setattr(agent_executor_module.httpx, "AsyncClient", _FakeAsyncClient)

    worker = _FakeWorker(result=_sample_result())
    part = Part(url="http://files.local/orders/po-7.pdf", filename="po-7.pdf")
    queue = _RecordingQueue()

    await ProcessOrderAgentExecutor(worker).execute(_context(_message([part])), queue)

    assert fetched == ["http://files.local/orders/po-7.pdf"]
    assert worker.calls == [(b"%PDF-1.4 remote", "po-7.pdf")]


@pytest.mark.asyncio
async def test_cancel_is_unsupported() -> None:
    with pytest.raises(UnsupportedOperationError):
        await ProcessOrderAgentExecutor(_FakeWorker()).cancel(_context(_message([_pdf_part()])), _RecordingQueue())
