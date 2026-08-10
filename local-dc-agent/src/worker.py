import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import mlflow

from .fulfillment import FulfillmentError, fulfill_order
from .ingest_client import IngestError, convert_pdf_to_markdown
from .mcp_tools import McpToolRouter
from .models import ProcessOrderResult
from .order_extraction import ExtractionError, extract_order
from .order_processing import process_order
from .tracing import configure_tracing

configure_tracing()

logger = logging.getLogger(__name__)

OnEvent = Callable[[str, dict], Awaitable[None]]


@dataclass
class ProcessingJob:
    pdf_bytes: bytes
    filename: str
    future: asyncio.Future[ProcessOrderResult]
    on_event: OnEvent | None = None


class OrderWorker:
    """Serially processes queued purchase orders through ingest, extraction, and
    fulfillment. The processing loop blocks on an empty queue between jobs, so no
    LLM call is ever made while there is nothing to do."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[ProcessingJob] = asyncio.Queue()
        self._router = McpToolRouter()
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()

    async def start(self) -> None:
        # Connecting to the downstream MCP servers can block for a while (it
        # retries internally -- see McpToolRouter._connect_with_retry) if this
        # pod came up before they did, e.g. during a fresh deploy or a cluster
        # restart. Run that -- and the queue-draining loop it gates -- as a
        # background task so ASGI/app startup itself returns immediately: the
        # process starts serving HTTP (health checks, agent card) right away,
        # and is_ready() reports the real MCP-connectivity state in the
        # meantime instead of the whole app failing to start.
        self._task = asyncio.create_task(self._connect_and_run())
        logger.info("Order worker starting (connecting to MCP servers in the background)")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            # Wait for the task to actually unwind (rather than closing the
            # router here) so McpToolRouter.close() runs inside the same task
            # that opened its connections -- anyio's cancel scopes require
            # being entered and exited from the same asyncio task, so closing
            # them from this (different) task raises RuntimeError.
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                # A cancellation landing mid-connection-attempt can surface as
                # an ExceptionGroup from anyio's task-group teardown (e.g. a
                # connection error that was already in flight) rather than a
                # clean CancelledError. That's a discarded, in-progress
                # connection attempt being abandoned -- not a reason to fail
                # shutdown -- so log it and move on.
                logger.exception("Order worker's background task raised while shutting down")
            self._task = None
        logger.info("Order worker stopped")

    def is_ready(self) -> bool:
        return self._ready.is_set()

    async def submit(
        self, pdf_bytes: bytes, filename: str, on_event: OnEvent | None = None
    ) -> ProcessOrderResult:
        logger.info("Job submitted: %s (%d bytes)", filename, len(pdf_bytes))
        future: asyncio.Future[ProcessOrderResult] = asyncio.get_running_loop().create_future()
        await self._queue.put(
            ProcessingJob(pdf_bytes=pdf_bytes, filename=filename, future=future, on_event=on_event)
        )
        return await future

    async def _connect_and_run(self) -> None:
        try:
            await self._router.connect()
            self._ready.set()
            logger.info("Order worker connected to all MCP servers; processing queued jobs")
            await self._run()
        except asyncio.CancelledError:
            raise
        except BaseException:
            # Nothing else ever awaits this background task except stop() at
            # shutdown, so an exception escaping here (this has happened
            # before -- see McpToolRouter._connect_with_retry's docstring for
            # the specific anyio-cancellation failure mode that slipped past
            # its retry loop) would otherwise die completely silently: the
            # process keeps serving /health, /ready stays stuck at "starting"
            # forever, and there is zero trace in the logs of why.
            logger.exception("Order worker's connect/run loop died unexpectedly")
            raise
        finally:
            await self._router.close()

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()  # idle here; no LLM call until a job exists
            try:
                result = await self._process(job)
                job.future.set_result(result)
            except (IngestError, ExtractionError, FulfillmentError) as exc:
                logger.exception("Job failed: %s", job.filename)
                job.future.set_exception(exc)
            except Exception as exc:  # noqa: BLE001 - one bad job must not wedge the queue forever
                logger.exception("Unexpected error processing job: %s", job.filename)
                job.future.set_exception(exc)
            finally:
                self._queue.task_done()

    async def _process(self, job: ProcessingJob) -> ProcessOrderResult:
        with mlflow.start_span(name="process_purchase_order", span_type="CHAIN") as span:
            span.set_inputs({"filename": job.filename, "pdf_bytes": len(job.pdf_bytes)})
            mlflow.update_current_trace(tags={"po.filename": job.filename})
            result = await self._process_traced(job)
            span.set_outputs(
                {
                    "po_number": result.po_number,
                    "dc_order_id": result.dc_order_id,
                    "order_status": result.fulfillment.order_status if result.fulfillment else None,
                }
            )
            return result

    async def _process_traced(self, job: ProcessingJob) -> ProcessOrderResult:
        on_event = job.on_event

        markdown = await convert_pdf_to_markdown(job.pdf_bytes, job.filename)
        logger.info("Ingested %s -> %d chars of markdown", job.filename, len(markdown))
        if on_event:
            await on_event(
                "ingested",
                {"filename": job.filename, "markdown_length": len(markdown), "markdown": markdown},
            )

        extracted = await asyncio.to_thread(extract_order, markdown)
        logger.info(
            "Extracted PO %s from %s: %d line item(s)",
            extracted.po_number, job.filename, len(extracted.line_items),
        )
        mlflow.update_current_trace(tags={"po.number": extracted.po_number})
        if on_event:
            await on_event(
                "extracted",
                {
                    "po_number": extracted.po_number,
                    "vendor_name": extracted.vendor_name,
                    "buyer_name": extracted.buyer_name,
                    "ship_to": extracted.ship_to,
                    "line_items": [item.model_dump() for item in extracted.line_items],
                },
            )

        result = process_order(extracted)
        logger.info(
            "Processed PO %s as %s (totals_mismatch=%s)",
            extracted.po_number, result.dc_order_id, result.totals_mismatch,
        )
        mlflow.update_current_trace(tags={"po.dc_order_id": result.dc_order_id})
        if on_event:
            await on_event(
                "processed",
                {
                    "dc_order_id": result.dc_order_id,
                    "computed_subtotal": result.computed_subtotal,
                    "stated_total": result.stated_total,
                    "totals_mismatch": result.totals_mismatch,
                },
            )

        fulfillment = await fulfill_order(result, self._router, on_event=on_event)
        logger.info("Fulfilled order %s: status=%s", result.dc_order_id, fulfillment.order_status)
        return result.model_copy(update={"fulfillment": fulfillment})
