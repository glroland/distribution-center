from datetime import datetime, timezone
from pathlib import Path

from .models import PurchaseOrderFile
from .settings import settings


class PurchaseOrderNotFoundError(Exception):
    pass


def list_purchase_orders() -> list[PurchaseOrderFile]:
    files: dict[str, Path] = {}
    for directory in settings.po_dirs():
        if not directory.is_dir():
            continue
        for path in directory.glob("*.pdf"):
            files.setdefault(path.name, path)

    orders = [
        PurchaseOrderFile(
            po_number=path.stem,
            filename=path.name,
            size_bytes=path.stat().st_size,
            modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        )
        for path in files.values()
    ]
    orders.sort(key=lambda po: po.modified_at, reverse=True)
    return orders


def resolve_purchase_order_path(filename: str) -> Path:
    """Resolves a filename (as returned by list_purchase_orders) to its file on disk.
    Only bare filenames are accepted - this rejects any path component so a caller
    can't escape the configured PO directories."""
    safe_name = Path(filename).name
    if safe_name != filename:
        raise PurchaseOrderNotFoundError(filename)

    for directory in settings.po_dirs():
        candidate = directory / safe_name
        if candidate.is_file():
            return candidate

    raise PurchaseOrderNotFoundError(filename)


def read_purchase_order_bytes(filename: str) -> bytes:
    return resolve_purchase_order_path(filename).read_bytes()
