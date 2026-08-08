"""Builds evaluation cases with a known-correct answer, for the two
benchmarks that need one (extraction accuracy, end-to-end outcome). Renders
real PDFs via pdf_builder so each case exercises the actual po-ingest-api ->
extract_order() path, not a shortcut around it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from .pdf_builder import PdfLineItem, build_po_pdf
from .seed_data import ExpectedStock, load_catalog, load_expected_stock

_VENDORS = [
    ("Acme Industrial Supply", "400 Foundry Rd, Dayton, OH 45402"),
    ("Northwind Traders", "12 Harbor St, Duluth, MN 55802"),
    ("Blue Ridge Supply Co.", "88 Ridgeline Ave, Asheville, NC 28801"),
]
_BUYERS = [
    ("Cedarhollow Regional Warehouse", "900 Distribution Pkwy, Cedarhollow, TX 75001"),
    ("Meridian Retail Group", "77 Commerce Dr, Meridian, ID 83642"),
]


@dataclass(frozen=True)
class ExtractionCase:
    """One golden (PDF, expected-extraction) pair for the extraction benchmark."""

    case_id: str
    pdf_path: Path
    golden: dict


@dataclass(frozen=True)
class ScenarioLineExpectation:
    sku: str
    requested_qty: float
    expected_fulfilled_qty: float

    @property
    def expected_fully_fulfilled(self) -> bool:
        return self.expected_fulfilled_qty >= self.requested_qty


@dataclass(frozen=True)
class FulfillmentScenario:
    """One golden (PDF, expected fulfillment outcome) pair for the end-to-end benchmark."""

    scenario_id: str
    pdf_path: Path
    po_number: str
    line_items: list[ScenarioLineExpectation]
    # "shipped" = every line fully fulfilled; "short" = at least one line isn't --
    # order_status is scored coarsely (see scoring.py) rather than against an exact
    # enum literal, since e.g. "out_of_stock" vs "escalated" is a legitimate policy
    # choice the fulfillment prompt makes, not a fact this dataset can pin down.
    expect_fully_shipped: bool


def _po_number(prefix: str, index: int) -> str:
    return f"EVAL-{prefix}-{index:04d}"


def build_extraction_dataset(n: int, seed: int, output_dir: Path) -> list[ExtractionCase]:
    """Golden extraction cases: line items are sampled from the catalog (not gated
    on stock -- extraction accuracy doesn't depend on what's on a shelf), but every
    field placed on the PDF is known exactly, so the LLM's extraction can be diffed
    against ground truth field-by-field."""
    rng = random.Random(seed)
    catalog = load_catalog()
    skus = list(catalog)
    cases: list[ExtractionCase] = []

    for i in range(n):
        vendor_name, vendor_addr = rng.choice(_VENDORS)
        buyer_name, ship_to = rng.choice(_BUYERS)
        po_number = _po_number("EXTRACT", i)
        chosen_skus = rng.sample(skus, k=min(rng.randint(2, 5), len(skus)))
        line_items: list[PdfLineItem] = [
            {
                "sku": sku,
                "description": catalog[sku],
                "quantity": rng.randint(1, 20),
                "unit_price": round(rng.uniform(2.0, 500.0), 2),
            }
            for sku in chosen_skus
        ]

        pdf_path = output_dir / f"{po_number}.pdf"
        build_po_pdf(
            {
                "po_number": po_number,
                "issue_date": "2026-01-15",
                "vendor_name": f"{vendor_name}\n{vendor_addr}",
                "buyer_name": buyer_name,
                "ship_to": ship_to,
                "payment_terms": "Net 30",
                "line_items": line_items,
            },
            pdf_path,
        )

        golden = {
            "po_number": po_number,
            "vendor_name": vendor_name,
            "buyer_name": buyer_name,
            "ship_to": ship_to,
            "line_items": [
                {
                    "sku": item["sku"],
                    "description": item["description"],
                    "quantity": float(item["quantity"]),
                    "unit_price": item["unit_price"],
                }
                for item in line_items
            ],
        }
        cases.append(ExtractionCase(case_id=po_number, pdf_path=pdf_path, golden=golden))

    return cases


def _pick_stock(expected: dict[str, ExpectedStock], predicate) -> ExpectedStock:
    for stock in expected.values():
        if predicate(stock):
            return stock
    raise RuntimeError("seed data no longer has a SKU matching this scenario's requirement")


def build_fulfillment_scenarios(output_dir: Path) -> list[FulfillmentScenario]:
    """Two fixed, hand-picked scenarios that stay valid as long as the seed CSVs do
    (asserted via _pick_stock's predicates rather than hardcoded SKUs, so a reseed
    of inventory.csv/shelves.csv doesn't silently invalidate the ground truth)."""
    catalog = load_catalog()
    expected = load_expected_stock()
    scenarios: list[FulfillmentScenario] = []

    # Scenario 1: every line item comfortably in stock -> should ship complete.
    well_stocked = [
        s for s in expected.values() if s.on_hand_qty >= 10 and s.shelf_qty >= 10
    ][:3]
    if len(well_stocked) < 2:
        raise RuntimeError("need at least 2 well-stocked SKUs in seed data for the 'clean' scenario")
    po_number = _po_number("CLEAN", 1)
    line_items: list[PdfLineItem] = []
    expectations: list[ScenarioLineExpectation] = []
    for stock in well_stocked:
        qty = 2
        line_items.append(
            {"sku": stock.sku, "description": catalog[stock.sku], "quantity": qty, "unit_price": 25.0}
        )
        expectations.append(
            ScenarioLineExpectation(sku=stock.sku, requested_qty=qty, expected_fulfilled_qty=qty)
        )
    pdf_path = output_dir / f"{po_number}.pdf"
    build_po_pdf(_scenario_pdf_order(po_number, line_items), pdf_path)
    scenarios.append(
        FulfillmentScenario(
            scenario_id="clean_full_stock",
            pdf_path=pdf_path,
            po_number=po_number,
            line_items=expectations,
            expect_fully_shipped=True,
        )
    )

    # Scenario 2: one well-stocked line (should ship), one line requested well
    # beyond both its WMS on-hand and shelf qty (guaranteed shortfall regardless of
    # which store gates fulfillment), and one catalog-only SKU stocked nowhere
    # (guaranteed zero-fulfillable) -> should ship the first line and escalate the
    # other two.
    scarce = _pick_stock(expected, lambda s: 0 < s.on_hand_qty <= 10 and 0 < s.shelf_qty <= 10)
    unstocked = _pick_stock(expected, lambda s: s.on_hand_qty == 0 and s.shelf_qty == 0)
    po_number = _po_number("MIXED", 1)
    scarce_requested = scarce.on_hand_qty + 10  # comfortably more than either store has
    mixed_line_items: list[PdfLineItem] = [
        {
            "sku": well_stocked[0].sku,
            "description": catalog[well_stocked[0].sku],
            "quantity": 2,
            "unit_price": 25.0,
        },
        {
            "sku": scarce.sku,
            "description": catalog[scarce.sku],
            "quantity": scarce_requested,
            "unit_price": 15.0,
        },
        {
            "sku": unstocked.sku,
            "description": catalog[unstocked.sku],
            "quantity": 5,
            "unit_price": 10.0,
        },
    ]
    mixed_expectations = [
        ScenarioLineExpectation(sku=well_stocked[0].sku, requested_qty=2, expected_fulfilled_qty=2),
        ScenarioLineExpectation(
            sku=scarce.sku,
            requested_qty=scarce_requested,
            expected_fulfilled_qty=scarce.expected_fulfillable(scarce_requested),
        ),
        ScenarioLineExpectation(sku=unstocked.sku, requested_qty=5, expected_fulfilled_qty=0),
    ]
    pdf_path = output_dir / f"{po_number}.pdf"
    build_po_pdf(_scenario_pdf_order(po_number, mixed_line_items), pdf_path)
    scenarios.append(
        FulfillmentScenario(
            scenario_id="mixed_shortfall_and_unknown",
            pdf_path=pdf_path,
            po_number=po_number,
            line_items=mixed_expectations,
            expect_fully_shipped=False,
        )
    )

    return scenarios


def _scenario_pdf_order(po_number: str, line_items: list[PdfLineItem]) -> dict:
    return {
        "po_number": po_number,
        "issue_date": "2026-01-15",
        "vendor_name": "Eval Suite Test Vendor\n1 Test Way, Springfield, IL 62701",
        "buyer_name": "Eval Suite Test Buyer",
        "ship_to": "500 Distribution Way, Springfield, IL 62701",
        "payment_terms": "Net 30",
        "line_items": line_items,
    }
