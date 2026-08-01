from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from .settings import settings

PROCESS_PURCHASE_ORDER_SKILL = AgentSkill(
    id="process_purchase_order",
    name="Process Purchase Order",
    description=(
        "Ingests a purchase order PDF, extracts its structured fields "
        "(PO number, vendor, line items, totals), checks warehouse inventory, "
        "dispatches the picking robot, ships whatever was retrieved, and "
        "returns a processed order record with carrier tracking numbers (or "
        "supervisor escalations for anything out of stock)."
    ),
    tags=["orders", "purchase-order", "pdf", "distribution-center", "fulfillment", "shipping"],
    examples=["Process this purchase order PDF for receiving and fulfillment."],
    input_modes=["application/pdf"],
    output_modes=["application/json", "text/plain"],
)

AGENT_CARD = AgentCard(
    name="Distribution Center Agent",
    description=(
        "Handles inbound purchase order processing and fulfillment for a "
        "distribution center: parses a PO PDF, checks inventory, drives a "
        "picking robot, ships what's available, and escalates shortages to "
        "a human supervisor."
    ),
    url=settings.AGENT_URL,
    version="0.1.0",
    capabilities=AgentCapabilities(streaming=False),
    default_input_modes=["application/pdf"],
    default_output_modes=["application/json", "text/plain"],
    skills=[PROCESS_PURCHASE_ORDER_SKILL],
)
