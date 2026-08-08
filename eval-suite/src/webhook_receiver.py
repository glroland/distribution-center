"""A tiny local HTTP receiver for local-dc-agent's `progress_webhook` events
(see local-dc-agent/src/agent_executor.py:_build_progress_hook and
dashboard-ui's SSE fan-out, which is the same mechanism this reuses).
Deliberately stdlib-only (http.server) rather than pulling in a second ASGI
stack just for a background test fixture.

The mcp-trajectory benchmark points a real dc-agent run at this receiver to
capture the exact tool-call stream (name, arguments, ok, result) the agent
produced, with wall-clock receive timestamps for latency scoring."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class WebhookReceiver:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer((host, port), self._make_handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "WebhookReceiver":
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/"

    def events(self) -> list[dict]:
        with self._lock:
            return list(self._events)

    def events_of_type(self, event_type: str) -> list[dict]:
        return [e for e in self.events() if e.get("type") == event_type]

    def _record(self, payload: dict) -> None:
        with self._lock:
            self._events.append({**payload, "received_at": time.monotonic()})

    def _make_handler(self):
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib naming convention
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    payload = {"type": "invalid_json", "data": {"raw": body.decode("utf-8", "replace")}}
                receiver._record(payload)
                self.send_response(200)
                self.end_headers()

            def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
                pass  # keep this quiet; eval-suite does its own logging

        return Handler
