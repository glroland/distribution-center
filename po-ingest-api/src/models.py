from typing import Any

from pydantic import BaseModel


class ConversionResult(BaseModel):
    filename: str
    markdown: str
    document: dict[str, Any]
