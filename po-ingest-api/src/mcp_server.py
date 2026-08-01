import base64

from mcp.server import MCPServer

from .conversion import convert_pdf

mcp_server = MCPServer(
    name="po-ingest-api",
    instructions="Converts PDF documents to Markdown using Docling.",
)


@mcp_server.tool()
def convert_pdf_to_markdown(pdf_base64: str, filename: str = "document.pdf") -> str:
    """Convert a base64-encoded PDF into Markdown."""
    data = base64.b64decode(pdf_base64)
    markdown, _document = convert_pdf(data, filename)
    return markdown
