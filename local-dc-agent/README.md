# DC Agent

An [A2A](https://a2a-protocol.org/) (Agent2Agent) agent for a distribution
center. It is intentionally scoped to a single skill for now:

| Skill | Description |
|---|---|
| `process_purchase_order` | Ingests a purchase order PDF, extracts its structured fields, and processes it into a distribution center order record. |

More distribution center skills (inventory allocation, pick/pack, shipping)
are expected to be added later as separate skills on this same agent —
this is not meant to be the last one.

## How `process_purchase_order` works

1. The caller sends an A2A message with a PDF file part (a PO).
2. The PDF is converted to Markdown via
   [`po-ingest-api`](../po-ingest-api) (`PO_INGEST_API_URL`, default
   `http://localhost:8000`) — this agent does not embed Docling itself.
3. The Markdown is sent to OpenAI (`openai` SDK) with a forced tool call
   to extract structured fields: PO number, vendor, buyer, ship-to,
   payment terms, and line items. An LLM is used here (rather than
   per-template parsing) because `test-po-generator` produces a dozen
   visually distinct letterhead layouts, and more may be added.
4. The extracted order is processed: subtotal is recomputed from the line
   items, compared against any stated total (flagging `totals_mismatch` if
   they disagree), and a `dc_order_id` is assigned.
5. The result is returned as an A2A artifact: a `DataPart` with the full
   structured JSON and a `TextPart` human-readable summary. If ingestion or
   extraction fails, the task is marked `failed` with an explanatory
   message instead.

## Setup

```bash
cd local-dc-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# for running tests
pip install -r requirements-dev.txt
```

Set `OPENAI_API_KEY` before running the agent for real (extraction will
fail without it; this is not needed to run the test suite, which mocks
the OpenAI and po-ingest-api calls). Settings are defined in
[`src/settings.py`](src/settings.py) with reasonable defaults; copy
`.env.example` to `.env` and fill it in to override them, or set
environment variables directly (env vars take precedence over `.env`).

## Run

```bash
# in one terminal
cd po-ingest-api && python3 -m src

# in another
export OPENAI_API_KEY=...
cd local-dc-agent && python3 -m src
```

Starts the agent on `http://localhost:9100`. The agent card is served at
`http://localhost:9100/.well-known/agent-card.json`.

| Env var | Default | Description |
|---|---|---|
| `PO_INGEST_API_URL` | `http://localhost:8000` | Base URL of `po-ingest-api` |
| `OPENAI_API_KEY` | *(none)* | Required to run order extraction |
| `OPENAI_MODEL` | `gpt-5` | Model used for extraction |
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `9100` | Bind port |
| `AGENT_URL` | `http://localhost:{PORT}/` | URL advertised in the agent card |

## Usage

Generate a sample PO and send it to the agent's `message/send` JSON-RPC
endpoint as a base64-encoded file part:

```bash
PDF_B64=$(base64 -i ../target/pos/some-po.pdf)
curl -s http://localhost:9100/ \
  -H 'Content-Type: application/json' \
  -d "$(cat <<EOF
{
  "jsonrpc": "2.0", "id": "1", "method": "message/send",
  "params": {
    "message": {
      "role": "user", "messageId": "m1", "kind": "message",
      "parts": [{"kind": "file", "file": {"bytes": "$PDF_B64", "mimeType": "application/pdf", "name": "po.pdf"}}]
    }
  }
}
EOF
)"
```

The response's `result.artifacts[0].parts` contains the structured
`ProcessOrderResult` JSON (`DataPart`) and a human-readable summary
(`TextPart`).

## Tests

```bash
pytest
```

Unit tests cover the order-processing logic (subtotal computation,
totals-mismatch detection) and the agent executor's event flow (success,
missing-PDF, ingest-failure, and extraction-failure paths), with the
`po-ingest-api` and OpenAI calls mocked — no network access,
`OPENAI_API_KEY`, or running services required.
