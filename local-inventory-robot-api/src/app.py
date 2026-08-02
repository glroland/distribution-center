from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, HTTPException

from .mcp_server import build_mcp_server
from .models import (
    DeliverResponse,
    ItemLocationResponse,
    LocationResponse,
    MoveRequest,
    PickRequest,
    ResetResponse,
    RestockRequest,
    RobotStatusResponse,
    ShelfStockResponse,
)
from .robot import (
    CapacityExceededError,
    InsufficientQuantityError,
    InvalidRestockLocationError,
    InventoryRobot,
    NotAtDockError,
    OutOfBoundsError,
    RobotStatus,
    ShelfSpaceExhaustedError,
    SkuNotAtLocationError,
)
from .settings import settings
from .tracing import configure_tracing

configure_tracing()
robot = InventoryRobot(
    settings.shelves_csv_path(),
    settings.GRID_WIDTH,
    settings.GRID_HEIGHT,
    (settings.DOCK_X, settings.DOCK_Y),
    settings.CARRY_CAPACITY,
    move_step_delay=settings.MOVE_STEP_DELAY_SECONDS,
)
mcp_server = build_mcp_server(robot)
mcp_app = mcp_server.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
        yield


app = FastAPI(title="Local Inventory Robot API", lifespan=lifespan)
app.mount("/mcp", mcp_app)


def _to_status_response(status: RobotStatus) -> RobotStatusResponse:
    return RobotStatusResponse(
        x=status.x,
        y=status.y,
        carrying=status.carrying,
        capacity=status.capacity,
        carrying_total=status.carrying_total,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/location", response_model=LocationResponse)
def get_location() -> LocationResponse:
    x, y = robot.get_location()
    return LocationResponse(x=x, y=y)


@app.get("/status", response_model=RobotStatusResponse)
def get_status() -> RobotStatusResponse:
    return _to_status_response(robot.get_status())


@app.post("/move", response_model=RobotStatusResponse)
async def move(body: MoveRequest) -> RobotStatusResponse:
    try:
        status = await robot.move_to((body.x, body.y))
    except OutOfBoundsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_status_response(status)


@app.get("/shelf", response_model=ShelfStockResponse)
def get_shelf(x: int | None = None, y: int | None = None) -> ShelfStockResponse:
    if (x is None) != (y is None):
        raise HTTPException(status_code=400, detail="x and y must both be given, or both omitted")
    location = (x, y) if x is not None and y is not None else None
    loc_x, loc_y = location if location is not None else robot.get_location()
    return ShelfStockResponse(location_x=loc_x, location_y=loc_y, stock=robot.get_shelf_stock(location))


@app.get("/find/{sku}", response_model=list[ItemLocationResponse])
def find_item(sku: str) -> list[ItemLocationResponse]:
    return [
        ItemLocationResponse(location_x=x, location_y=y, qty=qty)
        for (x, y), qty in robot.find_item(sku)
    ]


@app.post("/pick", response_model=RobotStatusResponse)
def pick(body: PickRequest) -> RobotStatusResponse:
    try:
        status = robot.pick(body.sku, body.qty)
    except SkuNotAtLocationError:
        raise HTTPException(
            status_code=404, detail=f"{body.sku} is not stocked at the robot's current location"
        ) from None
    except (InsufficientQuantityError, CapacityExceededError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _to_status_response(status)


@app.post("/restock", response_model=ShelfStockResponse)
def restock(body: RestockRequest) -> ShelfStockResponse:
    if (body.x is None) != (body.y is None):
        raise HTTPException(status_code=400, detail="x and y must both be given, or both omitted")
    location = (body.x, body.y) if body.x is not None and body.y is not None else None
    try:
        loc_x, loc_y = robot.restock(body.sku, body.qty, location)
    except InvalidRestockLocationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ShelfSpaceExhaustedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return ShelfStockResponse(location_x=loc_x, location_y=loc_y, stock=robot.get_shelf_stock((loc_x, loc_y)))


@app.post("/deliver", response_model=DeliverResponse)
def deliver() -> DeliverResponse:
    try:
        delivered, status = robot.deliver()
    except NotAtDockError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return DeliverResponse(delivered=delivered, status=_to_status_response(status))


@app.post("/reset", response_model=ResetResponse)
def reset() -> ResetResponse:
    robot.reset()
    return ResetResponse(status="ok")
