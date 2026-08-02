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
