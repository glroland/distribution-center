import itertools
import logging
import random
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class InvalidShipmentError(ValueError):
    """Raised when shipment creation is requested with invalid input."""


class ShipmentNotFoundError(KeyError):
    """Raised when a shipment id does not exist."""


class TrackingNumberNotFoundError(KeyError):
    """Raised when no shipment matches a tracking number."""


# Carrier name -> (min transit days, max transit days) used to mock an estimated delivery date.
_CARRIER_TRANSIT_DAYS = {
    "UPS": (2, 5),
    "FedEx": (1, 3),
    "USPS": (3, 7),
    "DHL": (4, 8),
}


def _generate_tracking_number(carrier: str) -> str:
    """Produce a tracking number in a carrier-plausible format. Not a real carrier format."""
    if carrier == "UPS":
        return "1Z" + "".join(random.choices(string.ascii_uppercase + string.digits, k=16))
    if carrier == "FedEx":
        return "".join(random.choices(string.digits, k=12))
    if carrier == "USPS":
        return "9400" + "".join(random.choices(string.digits, k=18))
    return "".join(random.choices(string.digits, k=10))  # DHL


@dataclass
class ShipmentItem:
    sku: str
    qty: int


@dataclass
class Shipment:
    id: int
    po_number: str
    customer_name: str
    customer_address: str
    items: list[ShipmentItem]
    carrier: str
    tracking_number: str
    status: str
    shipped_at: datetime
    estimated_delivery: datetime


class ShippingStore:
    """In-memory record of mock shipments dispatched to fulfil purchase orders.

    No real carrier is contacted. Creating a shipment randomly assigns a
    carrier, generates a carrier-plausible tracking number, and estimates a
    delivery date from that carrier's typical transit time. State is held
    entirely in memory and lost on restart, or can be discarded via `reset()`.
    """

    def __init__(self) -> None:
        self._shipments: list[Shipment] = []
        self._id_counter = itertools.count(1)

    def create_shipment(
        self,
        po_number: str,
        customer_name: str,
        customer_address: str,
        items: list[tuple[str, int]],
    ) -> Shipment:
        if not po_number or not po_number.strip():
            raise InvalidShipmentError("po_number must not be empty")
        if not customer_name or not customer_name.strip():
            raise InvalidShipmentError("customer_name must not be empty")
        if not customer_address or not customer_address.strip():
            raise InvalidShipmentError("customer_address must not be empty")
        if not items:
            raise InvalidShipmentError("items must not be empty")
        for sku, qty in items:
            if qty <= 0:
                logger.warning("Rejected shipment for PO %s: non-positive qty %d for %s", po_number, qty, sku)
                raise InvalidShipmentError(f"qty for {sku} must be positive")

        carrier = random.choice(list(_CARRIER_TRANSIT_DAYS))
        min_days, max_days = _CARRIER_TRANSIT_DAYS[carrier]
        shipped_at = datetime.now(timezone.utc)
        estimated_delivery = shipped_at + timedelta(days=random.randint(min_days, max_days))

        shipment = Shipment(
            id=next(self._id_counter),
            po_number=po_number,
            customer_name=customer_name,
            customer_address=customer_address,
            items=[ShipmentItem(sku=sku, qty=qty) for sku, qty in items],
            carrier=carrier,
            tracking_number=_generate_tracking_number(carrier),
            status="shipped",
            shipped_at=shipped_at,
            estimated_delivery=estimated_delivery,
        )
        self._shipments.append(shipment)
        logger.info(
            "Created shipment %d for PO %s: %s, tracking %s, %d item line(s)",
            shipment.id, po_number, carrier, shipment.tracking_number, len(shipment.items),
        )
        return shipment

    def list_shipments(self, po_number: str | None = None) -> list[Shipment]:
        if po_number is None:
            return list(self._shipments)
        return [s for s in self._shipments if s.po_number == po_number]

    def get_shipment(self, shipment_id: int) -> Shipment:
        for shipment in self._shipments:
            if shipment.id == shipment_id:
                return shipment
        raise ShipmentNotFoundError(shipment_id)

    def get_shipment_by_tracking(self, tracking_number: str) -> Shipment:
        for shipment in self._shipments:
            if shipment.tracking_number == tracking_number:
                return shipment
        raise TrackingNumberNotFoundError(tracking_number)

    def reset(self) -> None:
        """Clear all shipments. Intended for test isolation."""
        self._shipments = []
        self._id_counter = itertools.count(1)
