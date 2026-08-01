"""CLI entrypoint for generating sample purchase order PDFs."""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from faker import Faker

from src.catalog import load_catalog, random_line_items
from src.models import PurchaseOrder
from src.numbering import (
    random_buyer_contact,
    random_issue_date,
    random_payment_terms,
    random_po_number,
    random_ship_to,
    random_vendor,
)
from src.templates import get_random_template
from src.templates.base import fmt_currency

DEFAULT_CATALOG = Path(__file__).resolve().parent.parent / "products.csv"
DEFAULT_OUTPUT_DIR = Path("output")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="src",
        description="Generate sample purchase order PDFs from randomly selected fictional company templates.",
    )
    parser.add_argument(
        "--catalog",
        "-c",
        default=str(DEFAULT_CATALOG),
        help=f"Path to the product catalog CSV (columns: sku,description). Default: {DEFAULT_CATALOG}",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Directory to write generated PDFs to. Created if missing. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--count",
        "-n",
        type=int,
        default=1,
        help="Number of purchase orders to generate. Default: 1",
    )
    parser.add_argument(
        "--min-items",
        type=int,
        default=3,
        help="Minimum number of line items per PO. Default: 3",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=8,
        help="Maximum number of line items per PO. Default: 8",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible output. Default: unseeded (random each run).",
    )

    args = parser.parse_args(argv)

    if args.count < 1:
        parser.error("--count must be at least 1")
    if args.min_items < 1:
        parser.error("--min-items must be at least 1")
    if args.max_items < args.min_items:
        parser.error("--max-items must be >= --min-items")

    return args


def generate_one(catalog: list[dict[str, str]], args: argparse.Namespace, output_dir: Path) -> Path:
    template = get_random_template()
    buyer = template.company

    line_items = random_line_items(catalog, args.min_items, args.max_items)
    ship_to = random_ship_to(buyer)

    po = PurchaseOrder(
        po_number=random_po_number(),
        issue_date=random_issue_date(),
        buyer=buyer,
        vendor=random_vendor(),
        ship_to=ship_to,
        payment_terms=random_payment_terms(),
        buyer_contact=random_buyer_contact(),
        line_items=line_items,
    )

    output_path = output_dir / f"{po.po_number}.pdf"
    template.render(po, str(output_path))

    print(f"{output_path.name}  |  {buyer.name}  |  {len(line_items)} items  |  {fmt_currency(po.total)}")
    return output_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.seed is not None:
        random.seed(args.seed)
        Faker.seed(args.seed)

    try:
        catalog = load_catalog(args.catalog)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for _ in range(args.count):
        generate_one(catalog, args, output_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
