import itertools
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Other distribution centers a transfer might be sourced from. Purely
# cosmetic for this demo - none of these are real, addressable services.
_TRANSFER_SOURCE_LOCATIONS = ["DC-North", "DC-South", "DC-East", "DC-West"]


class HelpRequestNotFoundError(KeyError):
    """Raised when a help request id does not exist."""


class HelpRequestAlreadyResolvedError(ValueError):
    """Raised when attempting to resolve a help request that is already resolved."""


class TransferRequestNotFoundError(KeyError):
    """Raised when a transfer request id does not exist."""


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


@dataclass
class TransferRequest:
    id: int
    agent_id: str | None
    sku: str
    quantity: int
    context: str | None
    status: str  # "available" or "unavailable"
    source_location: str | None
    created_at: datetime


class SupervisorStore:
    """In-memory list of help requests raised by AI agents that get stuck."""

    def __init__(self, unavailable_chance: float = 1 / 3) -> None:
        self._requests: list[HelpRequest] = []
        self._id_counter = itertools.count(1)
        self._unavailable_chance = unavailable_chance
        self._transfer_requests: list[TransferRequest] = []
        self._transfer_id_counter = itertools.count(1)
        self._rng = random.Random()

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

    def create_transfer_request(
        self, sku: str, quantity: int, agent_id: str | None = None, context: str | None = None
    ) -> TransferRequest:
        if not sku or not sku.strip():
            raise ValueError("sku must not be empty")
        if quantity <= 0:
            raise ValueError("quantity must be positive")

        if self._rng.random() < self._unavailable_chance:
            status = "unavailable"
            source_location = None
        else:
            status = "available"
            source_location = self._rng.choice(_TRANSFER_SOURCE_LOCATIONS)

        request = TransferRequest(
            id=next(self._transfer_id_counter),
            agent_id=agent_id,
            sku=sku,
            quantity=quantity,
            context=context,
            status=status,
            source_location=source_location,
            created_at=datetime.now(timezone.utc),
        )
        self._transfer_requests.append(request)
        logger.info(
            "Transfer request %d created (agent_id=%s): %d x %s -> %s (source=%s)",
            request.id, agent_id, quantity, sku, status, source_location,
        )
        return request

    def list_transfer_requests(self, status: str | None = None) -> list[TransferRequest]:
        if status is None:
            return list(self._transfer_requests)
        return [r for r in self._transfer_requests if r.status == status]

    def get_transfer_request(self, request_id: int) -> TransferRequest:
        for request in self._transfer_requests:
            if request.id == request_id:
                return request
        raise TransferRequestNotFoundError(request_id)

    def reset(self) -> None:
        """Clear all help and transfer requests. Intended for test isolation."""
        self._requests = []
        self._id_counter = itertools.count(1)
        self._transfer_requests = []
        self._transfer_id_counter = itertools.count(1)
