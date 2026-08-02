import base64

from fastmcp import FastMCP

from .conversion import convert_pdf
from .tracing import configure_tracing, tool_trace

configure_tracing()
mcp_server = FastMCP(
    name="po-ingest-api",
    instructions="Converts PDF documents to Markdown using Docling.",
)


@mcp_server.tool()
@tool_trace
def convert_pdf_to_markdown(pdf_base64: str, filename: str = "document.pdf") -> str:
    """Convert a base64-encoded PDF into Markdown."""
    data = base64.b64decode(pdf_base64)
    markdown, _document = convert_pdf(data, filename)
    return markdown
