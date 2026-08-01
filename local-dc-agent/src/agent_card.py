from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from .settings import settings

PROCESS_PURCHASE_ORDER_SKILL = AgentSkill(
    id="process_purchase_order",
    name="Process Purchase Order",
    description=(
        "Ingests a purchase order PDF, extracts its structured fields "
        "(PO number, vendor, line items, totals), and processes it into a "
        "distribution center order record."
    ),
    tags=["orders", "purchase-order", "pdf", "distribution-center"],
    examples=["Process this purchase order PDF for receiving."],
    input_modes=["application/pdf"],
    output_modes=["application/json", "text/plain"],
)

AGENT_CARD = AgentCard(
    name="Distribution Center Agent",
    description=(
        "Handles inbound purchase order processing for a distribution center. "
        "Currently supports one skill: converting a PO PDF into a structured, "
        "processed order record. More distribution center capabilities "
        "(inventory allocation, pick/pack, shipping) are planned."
    ),
    url=settings.AGENT_URL,
    version="0.1.0",
    capabilities=AgentCapabilities(streaming=False),
    default_input_modes=["application/pdf"],
    default_output_modes=["application/json", "text/plain"],
    skills=[PROCESS_PURCHASE_ORDER_SKILL],
)
