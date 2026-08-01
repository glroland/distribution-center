import asyncio
import base64

import httpx
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import DataPart, FileWithBytes, FileWithUri, Part, TextPart, UnsupportedOperationError
from a2a.utils import get_file_parts, new_agent_text_message, new_task
from a2a.utils.errors import ServerError

from .ingest_client import IngestError, convert_pdf_to_markdown
from .order_extraction import ExtractionError, extract_order
from .order_processing import process_order, summarize


class ProcessOrderAgentExecutor(AgentExecutor):
    """Implements the distribution center's single skill: process_purchase_order."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if not task:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        pdf_file = _find_pdf_file(context.message.parts if context.message else [])
        if pdf_file is None:
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
            await updater.failed(
                new_agent_text_message(f"Could not read the PDF file: {exc}", task.context_id, task.id)
            )
            return

        filename = pdf_file.name or "purchase_order.pdf"

        try:
            markdown = await convert_pdf_to_markdown(pdf_bytes, filename)
        except IngestError as exc:
            await updater.failed(
                new_agent_text_message(f"Failed to ingest PDF: {exc}", task.context_id, task.id)
            )
            return

        try:
            extracted = await asyncio.to_thread(extract_order, markdown)
        except ExtractionError as exc:
            await updater.failed(
                new_agent_text_message(f"Failed to extract order data: {exc}", task.context_id, task.id)
            )
            return

        result = process_order(extracted)

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
