from pydantic import BaseModel, Field


class LocationResponse(BaseModel):
    x: int
    y: int


class RobotStatusResponse(BaseModel):
    x: int
    y: int
    carrying: dict[str, int]
    capacity: int
    carrying_total: int


class MoveRequest(BaseModel):
    x: int
    y: int


class ShelfStockResponse(BaseModel):
    location_x: int
    location_y: int
    stock: dict[str, int]


class ItemLocationResponse(BaseModel):
    location_x: int
    location_y: int
    qty: int


class PickRequest(BaseModel):
    sku: str
    qty: int = Field(gt=0)


class RestockRequest(BaseModel):
    sku: str
    qty: int = Field(gt=0)
    x: int | None = None
    y: int | None = None


class DeliverResponse(BaseModel):
    delivered: dict[str, int]
    status: RobotStatusResponse


class ResetResponse(BaseModel):
    status: str
