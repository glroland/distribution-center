import base64
import logging

import httpx
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    DataPart,
    FileWithBytes,
    FileWithUri,
    Message,
    Part,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import get_file_parts, new_agent_text_message, new_task
from a2a.utils.errors import ServerError

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
            task = new_task(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        pdf_file = _find_pdf_file(context.message.parts if context.message else [])
        if pdf_file is None:
            logger.warning("Task %s: no PDF file part found in the request", task.id)
            await updater.failed(
                new_agent_text_message(
                    "No PDF file part found in the request. Attach a purchase "
                    "order PDF to process it.",
                    task.context_id,
                    task.id,
                )
            )
            return

        try:
            pdf_bytes = await _load_pdf_bytes(pdf_file)
        except Exception as exc:
            logger.exception("Task %s: could not read the PDF file", task.id)
            await updater.failed(
                new_agent_text_message(f"Could not read the PDF file: {exc}", task.context_id, task.id)
            )
            return

        filename = pdf_file.name or "purchase_order.pdf"
        on_event = _build_progress_hook(context.message)

        logger.info("Task %s: processing %s", task.id, filename)
        try:
            result = await self.worker.submit(pdf_bytes, filename, on_event=on_event)
        except IngestError as exc:
            logger.exception("Task %s: failed to ingest %s", task.id, filename)
            await updater.failed(
                new_agent_text_message(f"Failed to ingest PDF: {exc}", task.context_id, task.id)
            )
            return
        except ExtractionError as exc:
            logger.exception("Task %s: failed to extract order data from %s", task.id, filename)
            await updater.failed(
                new_agent_text_message(f"Failed to extract order data: {exc}", task.context_id, task.id)
            )
            return
        except FulfillmentError as exc:
            logger.exception("Task %s: failed to fulfill order from %s", task.id, filename)
            await updater.failed(
                new_agent_text_message(f"Failed to fulfill order: {exc}", task.context_id, task.id)
            )
            return

        logger.info("Task %s: completed processing %s", task.id, filename)
        await updater.add_artifact(
            [
                Part(root=DataPart(data=result.model_dump())),
                Part(root=TextPart(text=summarize(result))),
            ],
            name="processed_order",
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(error=UnsupportedOperationError())


def _build_progress_hook(message: Message | None):
    """Builds an on_event callback that POSTs each processing event to a caller-supplied
    webhook URL, if the inbound message opted in via `metadata.progress_webhook`. This is
    purely additive: with no webhook configured, processing behaves exactly as before."""
    metadata = getattr(message, "metadata", None) or {}
    webhook_url = metadata.get("progress_webhook")
    if not webhook_url:
        return None

    async def _emit(event_type: str, data: dict) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(webhook_url, json={"type": event_type, "data": data})
        except Exception:
            pass  # a dashboard being slow or offline must never affect order processing

    return _emit


def _find_pdf_file(parts: list) -> FileWithBytes | FileWithUri | None:
    for file in get_file_parts(parts):
        is_pdf = file.mime_type == "application/pdf" or (file.name or "").lower().endswith(".pdf")
        if is_pdf:
            return file
    return None


async def _load_pdf_bytes(file: FileWithBytes | FileWithUri) -> bytes:
    if isinstance(file, FileWithBytes):
        return base64.b64decode(file.bytes)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(file.uri)
        response.raise_for_status()
        return response.content
