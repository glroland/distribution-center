import asyncio
from dataclasses import dataclass

from .fulfillment import FulfillmentError, fulfill_order
from .ingest_client import IngestError, convert_pdf_to_markdown
from .mcp_tools import McpToolRouter
from .models import ProcessOrderResult
from .order_extraction import ExtractionError, extract_order
from .order_processing import process_order


@dataclass
class ProcessingJob:
    pdf_bytes: bytes
    filename: str
    future: asyncio.Future[ProcessOrderResult]


class OrderWorker:
    """Serially processes queued purchase orders through ingest, extraction, and
    fulfillment. The processing loop blocks on an empty queue between jobs, so no
    LLM call is ever made while there is nothing to do."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[ProcessingJob] = asyncio.Queue()
        self._router = McpToolRouter()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self._router.connect()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        await self._router.close()

    async def submit(self, pdf_bytes: bytes, filename: str) -> ProcessOrderResult:
        future: asyncio.Future[ProcessOrderResult] = asyncio.get_running_loop().create_future()
        await self._queue.put(ProcessingJob(pdf_bytes=pdf_bytes, filename=filename, future=future))
        return await future

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()  # idle here; no LLM call until a job exists
            try:
                result = await self._process(job)
                job.future.set_result(result)
            except (IngestError, ExtractionError, FulfillmentError) as exc:
                job.future.set_exception(exc)
            finally:
                self._queue.task_done()

    async def _process(self, job: ProcessingJob) -> ProcessOrderResult:
        markdown = await convert_pdf_to_markdown(job.pdf_bytes, job.filename)
        extracted = await asyncio.to_thread(extract_order, markdown)
        result = process_order(extracted)
        fulfillment = await fulfill_order(result, self._router)
        return result.model_copy(update={"fulfillment": fulfillment})
