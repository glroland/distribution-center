from pydantic import BaseModel


class DistributionCenter(BaseModel):
    """The distribution center this dashboard sends purchase orders to - a
    self-contained set of services (dc-agent, WMS, robot, shipping). Mirrors the
    `distributionCenter` block in deploy/helm/values.yaml."""

    name: str
    display_name: str
    agent_url: str
    wms_url: str
    robot_url: str
    shipping_url: str
    grid_width: int
    grid_height: int
    dock_x: int
    dock_y: int


class PurchaseOrderFile(BaseModel):
    po_number: str
    filename: str
    size_bytes: int
    modified_at: str
