from datetime import datetime

from pydantic import BaseModel, Field


class ShipmentItemRequest(BaseModel):
    sku: str = Field(min_length=1)
    qty: int = Field(gt=0)


class ShipmentItemResponse(BaseModel):
    sku: str
    qty: int


class CreateShipmentRequest(BaseModel):
    po_number: str = Field(min_length=1)
    customer_name: str = Field(min_length=1)
    customer_address: str = Field(min_length=1)
    items: list[ShipmentItemRequest] = Field(min_length=1)


class ShipmentResponse(BaseModel):
    id: int
    po_number: str
    customer_name: str
    customer_address: str
    items: list[ShipmentItemResponse]
    carrier: str
    tracking_number: str
    status: str
    shipped_at: datetime
    estimated_delivery: datetime


class ResetResponse(BaseModel):
    status: str
    shipment_count: int
