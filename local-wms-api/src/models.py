from pydantic import BaseModel, Field


class InventoryItemResponse(BaseModel):
    sku: str
    on_hand_qty: int
    location_x: int
    location_y: int


class QuantityRequest(BaseModel):
    qty: int = Field(gt=0)


class BoostRequest(BaseModel):
    target_qty: int = Field(default=1_000_000, gt=0)


class BoostResponse(BaseModel):
    status: str
    changed: int


class LocationResponse(BaseModel):
    location_name: str


class ResetResponse(BaseModel):
    status: str
    item_count: int
