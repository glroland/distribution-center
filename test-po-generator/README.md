# Test PO Generator

Generates sample purchase order PDFs for use in demos. Each PDF is a real
structured document (selectable text, not a rasterized image), built with
[ReportLab](https://www.reportlab.com/). Every run randomly selects one of
12 distinct fictional company letterhead templates, then fills in random
line items (sampled from a product catalog CSV), quantities, prices, a
random vendor, and PO metadata.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
# Generate 1 PO using the default catalog (products.csv) into ./output
python -m src

# Generate 25 POs
python -m src --count 25

# Use a custom catalog and output directory
python -m src --catalog my_products.csv --output-dir /tmp/demo-pos --count 10

# Reproducible output (same seed -> same POs)
python -m src --count 5 --seed 42
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--catalog`, `-c` | `products.csv` (repo root) | CSV with `sku,description` columns |
| `--output-dir`, `-o` | `./output` | Directory PDFs are written to (created if missing) |
| `--count`, `-n` | `1` | Number of POs to generate |
| `--min-items` | `3` | Minimum line items per PO |
| `--max-items` | `8` | Maximum line items per PO |
| `--seed` | none | Random seed for reproducible runs |

## Product catalog CSV format

```csv
sku,description
SKU-1001,Wireless Barcode Scanner
SKU-1002,Heavy-Duty Shipping Tape (Case of 36)
```

Quantities and unit prices are randomized per PO at generation time (not
stored in the catalog).

## Templates

12 fictional companies, each with a distinct letterhead style, font, color
scheme, and table layout, live under `src/templates/`. One is
picked at random per generated PO:

- Acme Industrial Supply
- Northwind Traders
- Blue Ridge Supply Co.
- Summit Manufacturing
- Harborview Logistics
- Evergreen Office Solutions
- Ironclad Fabrication Co.
- Meridian Healthcare Supply
- Crestline Construction
- Pacific Tech Supply
- Golden State Foods Distribution
- City of Cedarhollow (municipal procurement)

To add a new template, create a new module in `src/templates/`
exporting a `COMPANY` (see `src/models.py:Company`) and a
`render(po, output_path)` function, then register it in
`src/templates/__init__.py`.
