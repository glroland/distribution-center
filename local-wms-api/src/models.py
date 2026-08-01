from pydantic import BaseModel, Field


class InventoryItemResponse(BaseModel):
    sku: str
    on_hand_qty: int
    location_x: int
    location_y: int


class QuantityRequest(BaseModel):
    qty: int = Field(gt=0)


class LocationResponse(BaseModel):
    location_name: str


class ResetResponse(BaseModel):
    status: str
    item_count: int
