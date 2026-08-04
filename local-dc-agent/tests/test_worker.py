import asyncio
import contextlib

import pytest

from src import worker as worker_module
from src.models import ExtractedOrder, FulfillmentResult, LineItem
from src.worker import OrderWorker


def _stub_pipeline(monkeypatch, extract_log: list, filename_as_po_number: bool = False):
    async def fake_convert(pdf_bytes: bytes, filename: str) -> str:
        return filename if filename_as_po_number else "# markdown"

    def fake_extract(markdown: str) -> ExtractedOrder:
        extract_log.append(markdown)
        return ExtractedOrder(
            po_number=markdown,
            line_items=[LineItem(description="Widget", quantity=1, unit_price=1.0)],
        )

    async def fake_fulfill(order, tools, on_event=None) -> FulfillmentResult:
        return FulfillmentResult(items=[], order_status="shipped", summary="done")

    monkeypatch.setattr(worker_module, "convert_pdf_to_markdown", fake_convert)
    monkeypatch.setattr(worker_module, "extract_order", fake_extract)
    monkeypatch.setattr(worker_module, "fulfill_order", fake_fulfill)


@pytest.mark.asyncio
async def test_worker_is_idle_until_a_job_is_submitted(monkeypatch) -> None:
    extract_log: list = []
    _stub_pipeline(monkeypatch, extract_log)

    worker = OrderWorker()
    run_task = asyncio.create_task(worker._run())
    try:
        await asyncio.sleep(0.05)
        assert extract_log == []  # idle queue.get() -- no extraction/LLM call happened

        result = await worker.submit(b"%PDF-fake", "po.pdf")

        assert extract_log == ["# markdown"]
        assert result.po_number == "# markdown"
        assert result.fulfillment.order_status == "shipped"
    finally:
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task


@pytest.mark.asyncio
async def test_on_event_fires_for_each_pipeline_stage(monkeypatch) -> None:
    extract_log: list = []
    _stub_pipeline(monkeypatch, extract_log)

    events: list[tuple[str, dict]] = []

    async def on_event(event_type: str, data: dict) -> None:
        events.append((event_type, data))

    worker = OrderWorker()
    run_task = asyncio.create_task(worker._run())
    try:
        await worker.submit(b"%PDF-fake", "po.pdf", on_event=on_event)

        assert [event_type for event_type, _ in events] == ["ingested", "extracted", "processed"]
        assert events[1][1]["po_number"] == "# markdown"
    finally:
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task


@pytest.mark.asyncio
async def test_start_does_not_block_on_slow_mcp_connect(monkeypatch) -> None:
    """A pod coming up before its downstream MCP servers do must not hang or
    fail app startup -- start() should return immediately and let connecting
    happen in the background, with is_ready() reporting the real state."""
    connect_gate = asyncio.Event()

    async def slow_connect() -> None:
        await connect_gate.wait()

    worker = OrderWorker()
    monkeypatch.setattr(worker._router, "connect", slow_connect)

    await asyncio.wait_for(worker.start(), timeout=1.0)
    assert worker.is_ready() is False

    connect_gate.set()
    await asyncio.sleep(0.05)
    assert worker.is_ready() is True

    await worker.stop()


@pytest.mark.asyncio
async def test_stop_does_not_propagate_errors_from_cancelling_a_slow_connect(monkeypatch) -> None:
    """Cancelling the background task while it's mid-connection-attempt can
    surface as something other than a clean CancelledError (in production,
    anyio's task-group teardown can turn it into an ExceptionGroup wrapping a
    connection error). stop() must swallow that -- it's tearing down a
    discarded, in-progress connection attempt, not a reason for pod shutdown
    itself to blow up."""

    async def raise_on_close() -> None:
        raise RuntimeError("simulated: anyio task-group teardown noise on cancel")

    worker = OrderWorker()
    monkeypatch.setattr(worker._router, "connect", asyncio.Event().wait)  # blocks forever until cancelled
    monkeypatch.setattr(worker._router, "close", raise_on_close)

    await worker.start()
    await asyncio.sleep(0.05)

    await worker.stop()  # must not raise

    assert worker._task is None


@pytest.mark.asyncio
async def test_jobs_submitted_before_ready_are_processed_once_connected(monkeypatch) -> None:
    extract_log: list = []
    _stub_pipeline(monkeypatch, extract_log)

    connect_gate = asyncio.Event()

    async def gated_connect() -> None:
        await connect_gate.wait()

    worker = OrderWorker()
    monkeypatch.setattr(worker._router, "connect", gated_connect)
    await worker.start()

    submit_future = asyncio.ensure_future(worker.submit(b"%PDF-fake", "po.pdf"))
    await asyncio.sleep(0.05)
    assert not submit_future.done()  # queued, waiting on MCP connectivity

    connect_gate.set()
    result = await asyncio.wait_for(submit_future, timeout=1.0)

    assert result.fulfillment.order_status == "shipped"
    await worker.stop()


@pytest.mark.asyncio
async def test_worker_processes_jobs_serially_in_submission_order(monkeypatch) -> None:
    extract_log: list = []
    _stub_pipeline(monkeypatch, extract_log, filename_as_po_number=True)

    worker = OrderWorker()
    run_task = asyncio.create_task(worker._run())
    try:
        first = asyncio.ensure_future(worker.submit(b"%PDF-fake", "PO-A"))
        second = asyncio.ensure_future(worker.submit(b"%PDF-fake", "PO-B"))

        result_a = await first
        result_b = await second

        assert extract_log == ["PO-A", "PO-B"]
        assert result_a.po_number == "PO-A"
        assert result_b.po_number == "PO-B"
    finally:
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task
