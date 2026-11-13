import logging

import httpx
from a2a.helpers import new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Part
from a2a.utils.errors import UnsupportedOperationError

from .fulfillment import FulfillmentError
from .ingest_client import IngestError
from .order_extraction import ExtractionError
from .order_processing import summarize
from .worker import OrderWorker

logger = logging.getLogger(__name__)


class ProcessOrderAgentExecutor(AgentExecutor):
    """Implements the distribution center's single skill: process_purchase_order.

    Hands each PDF off to a shared OrderWorker, which serializes ingest,
    extraction, and fulfillment through one persistent background loop rather
    than doing this work inline per HTTP request."""

    def __init__(self, worker: OrderWorker) -> None:
        self.worker = worker

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if not task:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        async def fail(text: str) -> None:
            await updater.failed(updater.new_agent_message([new_text_part(text)]))

        pdf_part = _find_pdf_part(context.message.parts if context.message else [])
        if pdf_part is None:
            logger.warning("Task %s: no PDF file part found in the request", task.id)
            await fail("No PDF file part found in the request. Attach a purchase order PDF to process it.")
            return

        try:
            pdf_bytes = await _load_pdf_bytes(pdf_part)
        except Exception as exc:
            logger.exception("Task %s: could not read the PDF file", task.id)
            await fail(f"Could not read the PDF file: {exc}")
            return

        filename = pdf_part.filename or "purchase_order.pdf"
        on_event = _build_progress_hook(context.message)

        logger.info("Task %s: processing %s", task.id, filename)
        try:
            result = await self.worker.submit(pdf_bytes, filename, on_event=on_event)
        except IngestError as exc:
            logger.exception("Task %s: failed to ingest %s", task.id, filename)
            await fail(f"Failed to ingest PDF: {exc}")
            return
        except ExtractionError as exc:
            logger.exception("Task %s: failed to extract order data from %s", task.id, filename)
            await fail(f"Failed to extract order data: {exc}")
            return
        except FulfillmentError as exc:
            logger.exception("Task %s: failed to fulfill order from %s", task.id, filename)
            await fail(f"Failed to fulfill order: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - surface as a failed task, not a hung request
            logger.exception("Task %s: unexpected error processing %s", task.id, filename)
            await fail(f"Unexpected error processing order: {exc}")
            return

        logger.info("Task %s: completed processing %s", task.id, filename)
        await updater.add_artifact(
            [
                # mode="json": the data part is a protobuf Value, which only
                # holds JSON types.
                new_data_part(result.model_dump(mode="json"), media_type="application/json"),
                new_text_part(summarize(result), media_type="text/plain"),
            ],
            name="processed_order",
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise UnsupportedOperationError()


def _build_progress_hook(message: Message | None):
    """Builds an on_event callback that POSTs each processing event to a caller-supplied
    webhook URL, if the inbound message opted in via `metadata.progress_webhook`. This is
    purely additive: with no webhook configured, processing behaves exactly as before."""
    # Message.metadata is a protobuf Struct: always present, empty when unset.
    metadata = message.metadata if message is not None else {}
    webhook_url = metadata["progress_webhook"] if "progress_webhook" in metadata else None
    if not webhook_url or not isinstance(webhook_url, str):
        return None

    async def _emit(event_type: str, data: dict) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(webhook_url, json={"type": event_type, "data": data})
        except Exception:
            pass  # a dashboard being slow or offline must never affect order processing

    return _emit


def _find_pdf_part(parts) -> Part | None:
    """The first file part that is a PDF. In A2A 1.0 a file is a Part carrying
    either `raw` bytes or a `url`, with its name/type on the part itself."""
    for part in parts:
        if part.WhichOneof("content") not in ("raw", "url"):
            continue
        is_pdf = part.media_type == "application/pdf" or part.filename.lower().endswith(".pdf")
        if is_pdf:
            return part
    return None


async def _load_pdf_bytes(part: Part) -> bytes:
    if part.WhichOneof("content") == "raw":
        return part.raw

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(part.url)
        response.raise_for_status()
        return response.content
