import itertools
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class HelpRequestNotFoundError(KeyError):
    """Raised when a help request id does not exist."""


class HelpRequestAlreadyResolvedError(ValueError):
    """Raised when attempting to resolve a help request that is already resolved."""


@dataclass
class HelpRequest:
    id: int
    agent_id: str | None
    question: str
    context: str | None
    status: str
    created_at: datetime
    resolved_at: datetime | None = None
    resolution: str | None = None


class SupervisorStore:
    """In-memory list of help requests raised by AI agents that get stuck."""

    def __init__(self) -> None:
        self._requests: list[HelpRequest] = []
        self._id_counter = itertools.count(1)

    def create_help_request(
        self, question: str, agent_id: str | None = None, context: str | None = None
    ) -> HelpRequest:
        if not question or not question.strip():
            raise ValueError("question must not be empty")
        request = HelpRequest(
            id=next(self._id_counter),
            agent_id=agent_id,
            question=question,
            context=context,
            status="open",
            created_at=datetime.now(timezone.utc),
        )
        self._requests.append(request)
        logger.info("Help request %d created (agent_id=%s): %s", request.id, agent_id, question)
        return request

    def list_help_requests(self, status: str | None = None) -> list[HelpRequest]:
        if status is None:
            return list(self._requests)
        return [r for r in self._requests if r.status == status]

    def get_help_request(self, request_id: int) -> HelpRequest:
        for request in self._requests:
            if request.id == request_id:
                return request
        raise HelpRequestNotFoundError(request_id)

    def resolve_help_request(self, request_id: int, resolution: str) -> HelpRequest:
        request = self.get_help_request(request_id)
        if request.status == "resolved":
            raise HelpRequestAlreadyResolvedError(
                f"help request {request_id} is already resolved"
            )
        request.status = "resolved"
        request.resolution = resolution
        request.resolved_at = datetime.now(timezone.utc)
        logger.info("Help request %d resolved: %s", request_id, resolution)
        return request

    def reset(self) -> None:
        """Clear all help requests. Intended for test isolation."""
        self._requests = []
        self._id_counter = itertools.count(1)
