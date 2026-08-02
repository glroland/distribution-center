import logging
import tempfile
from pathlib import Path
from typing import Any

from docling.document_converter import DocumentConverter

logger = logging.getLogger(__name__)

_converter = DocumentConverter()


def convert_pdf(data: bytes, filename: str) -> tuple[str, dict[str, Any]]:
    """Convert PDF bytes into (markdown, DoclingDocument JSON) via Docling."""
    logger.debug("Running Docling conversion for %r", filename)
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(data)
        tmp.flush()
        result = _converter.convert(Path(tmp.name))

    document = result.document
    return document.export_to_markdown(), document.export_to_dict()
