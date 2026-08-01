import os

PO_INGEST_API_URL = os.environ.get("PO_INGEST_API_URL", "http://localhost:8000")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "9100"))
AGENT_URL = os.environ.get("AGENT_URL", f"http://localhost:{PORT}/")
