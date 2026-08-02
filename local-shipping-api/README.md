# Local Shipping API

A small demo API that ships product a warehouse robot has gathered for a
purchase order out to the customer. Shipments are held entirely in memory in
a list - they are lost on restart. Creating a shipment mocks handing the
package to a carrier: it randomly assigns a carrier, generates a
carrier-plausible tracking number, and estimates a delivery date from that
carrier's typical transit time. No real carrier is contacted.

## Setup

```bash
cd local-shipping-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# for running tests
pip install -r requirements-dev.txt
```

## Run

```bash
python -m src
```

Starts the API on `http://0.0.0.0:8004`. Interactive docs are available at
`http://localhost:8004/docs`.

Settings are defined in [`src/settings.py`](src/settings.py) with reasonable
defaults; copy `.env.example` to `.env` and fill it in to override them, or
set environment variables directly (env vars take precedence over `.env`).

| Env var | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `8004` | Bind port |
| `MLFLOW_TRACKING_URI` | unset | MLflow tracking server URL. When set, every MCP tool call is traced as an MLflow span (see [`src/tracing.py`](src/tracing.py)); left unset, tracing is disabled outright. MLflow's own env vars (`MLFLOW_EXPERIMENT_NAME`, `MLFLOW_WORKSPACE`, `MLFLOW_TRACKING_TOKEN`, `MLFLOW_TRACKING_AUTH`, ...) are read natively by the `mlflow` package alongside this one |

## Carriers

Every shipment is randomly assigned one of four mock carriers, each with a
carrier-plausible tracking number format and a typical transit window used to
estimate the delivery date:

| Carrier | Tracking number format | Transit time |
|---|---|---|
| UPS | `1Z` + 16 alphanumeric characters | 2-5 days |
| FedEx | 12 digits | 1-3 days |
| USPS | `9400` + 18 digits | 3-7 days |
| DHL | 10 digits | 4-8 days |

## REST API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/shipments` | Body `{"po_number": str, "customer_name": str, "customer_address": str, "items": [{"sku": str, "qty": int}]}`; creates and ships an order |
| `GET` | `/shipments?po_number=` | List shipments, optionally filtered to a single PO number |
| `GET` | `/shipments/{id}` | Get a single shipment by id (404 if unknown) |
| `GET` | `/shipments/tracking/{tracking_number}` | Get a single shipment by tracking number (404 if unknown) |
| `POST` | `/shipments/reset` | Clear all shipments |

```bash
curl -X POST http://localhost:8004/shipments -H 'Content-Type: application/json' -d '{
  "po_number": "PO-1001",
  "customer_name": "Jane Doe",
  "customer_address": "123 Main St, Springfield",
  "items": [{"sku": "SKU-1001", "qty": 10}]
}'
```

## MCP

The service also exposes an MCP server (Streamable HTTP transport) at
`http://localhost:8004/mcp`, in the same process as the REST API, sharing the
same in-memory shipment store.

| Tool | Args | Description |
|---|---|---|
| `ship_order` | `po_number: str`, `customer_name: str`, `customer_address: str`, `items: list[{"sku": str, "qty": int}]` | Ship the given items to the customer, returning the created shipment |
| `get_shipment` | `shipment_id: int` | Look up a shipment by id |
| `track_shipment` | `tracking_number: str` | Look up a shipment by tracking number |
| `list_shipments` | `po_number: str \| None` | List shipments, optionally filtered to a PO number |
| `reset_shipments` | - | Clear all shipments |

A typical agent workflow: after a robot has picked and delivered every item
for a PO, call `ship_order` to dispatch it, then `track_shipment` or
`get_shipment` to check on it later.

Connect with any MCP client that supports Streamable HTTP, e.g.
`fastmcp.Client`.

## Tests

```bash
pytest
```

Covers the in-memory shipping store, the REST API, and the MCP tools.
