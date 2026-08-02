import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from .agent_client import AgentCallError, process_purchase_order
from .models import DistributionCenter
from .po_catalog import read_purchase_order_bytes
from .settings import settings

_RUN_RETENTION_SECONDS = 600


@dataclass
class Run:
    id: str
    dc_name: str
    po_filename: str
    events: list[dict] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    done: bool = False
    created_at: float = field(default_factory=time.time)

    async def publish(self, event_type: str, data: dict) -> None:
        event = {"type": event_type, "data": data, "ts": time.time()}
        self.events.append(event)
        for queue in list(self.subscribers):
            await queue.put(event)

    async def finish(self) -> None:
        self.done = True
        for queue in list(self.subscribers):
            await queue.put(None)


async def stream_events(run: Run) -> AsyncIterator[dict]:
    """Replays every event seen so far, then live-streams new ones until the run
    finishes. Safe to call more than once per run (e.g. on client reconnect)."""
    for event in list(run.events):
        yield event

    if run.done:
        return

    queue: asyncio.Queue = asyncio.Queue()
    run.subscribers.append(queue)
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
    finally:
        if queue in run.subscribers:
            run.subscribers.remove(queue)


class RunManager:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def start_run(self, dc: DistributionCenter, po_filename: str) -> Run:
        self._evict_stale()
        run = Run(id=uuid.uuid4().hex, dc_name=dc.name, po_filename=po_filename)
        self._runs[run.id] = run
        asyncio.create_task(self._execute(run, dc, po_filename))
        return run

    def _evict_stale(self) -> None:
        cutoff = time.time() - _RUN_RETENTION_SECONDS
        stale = [run_id for run_id, run in self._runs.items() if run.done and run.created_at < cutoff]
        for run_id in stale:
            del self._runs[run_id]

    async def _execute(self, run: Run, dc: DistributionCenter, po_filename: str) -> None:
        await run.publish("run_started", {"dc": dc.name, "po_filename": po_filename})
        try:
            pdf_bytes = read_purchase_order_bytes(po_filename)
        except Exception as exc:
            await run.publish("run_failed", {"error": f"Could not read {po_filename}: {exc}"})
            await run.finish()
            return

        webhook_url = f"{settings.PUBLIC_URL.rstrip('/')}/api/internal/events/{run.id}"
        try:
            outcome = await process_purchase_order(
                dc.agent_url, pdf_bytes, po_filename, progress_webhook=webhook_url
            )
            await run.publish("run_complete", outcome)
        except AgentCallError as exc:
            await run.publish("run_failed", {"error": str(exc)})
        except Exception as exc:
            await run.publish("run_failed", {"error": f"Unexpected error: {exc}"})
        finally:
            await run.finish()

    async def ingest_webhook_event(self, run_id: str, event_type: str, data: dict) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.done:
            return False
        await run.publish(event_type, data)
        return True


run_manager = RunManager()
