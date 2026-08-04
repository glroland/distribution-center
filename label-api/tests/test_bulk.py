import json
import zipfile
from pathlib import Path

from src.bulk import MANIFEST_FILENAME, generate_bulk_zip

_SIZE_KWARGS = dict(min_width=200, max_width=260, min_height=150, max_height=190)


def test_generate_bulk_zip_creates_one_image_per_quantity(tmp_path: Path) -> None:
    zip_path = generate_bulk_zip(
        [("sku-1001", 2), ("sku-1002", 1)],
        output_dir=tmp_path,
        **_SIZE_KWARGS,
    )

    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert len(names) == 3
    assert sum(name.startswith("SKU-1001") for name in names) == 2
    assert sum(name.startswith("SKU-1002") for name in names) == 1


def test_generate_bulk_zip_leaves_batch_folder_by_default(tmp_path: Path) -> None:
    zip_path = generate_bulk_zip(
        [("sku-1001", 1)],
        output_dir=tmp_path,
        cleanup_after_zip=False,
        **_SIZE_KWARGS,
    )
    batch_dir = tmp_path / zip_path.stem
    assert batch_dir.is_dir()
    assert len(list(batch_dir.iterdir())) == 1


def test_generate_bulk_zip_cleans_up_when_requested(tmp_path: Path) -> None:
    zip_path = generate_bulk_zip(
        [("sku-1001", 1)],
        output_dir=tmp_path,
        cleanup_after_zip=True,
        **_SIZE_KWARGS,
    )
    batch_dir = tmp_path / zip_path.stem
    assert not batch_dir.exists()
    assert zip_path.exists()


def test_generate_bulk_zip_sanitizes_sku_for_filenames(tmp_path: Path) -> None:
    zip_path = generate_bulk_zip(
        [("weird sku/name", 1)],
        output_dir=tmp_path,
        **_SIZE_KWARGS,
    )
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert len(names) == 1
    assert "/" not in names[0]


def test_generate_bulk_zip_omits_manifest_by_default(tmp_path: Path) -> None:
    zip_path = generate_bulk_zip(
        [("sku-1001", 2)],
        output_dir=tmp_path,
        **_SIZE_KWARGS,
    )
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert len(names) == 2
    assert MANIFEST_FILENAME not in names


def test_generate_bulk_zip_manifest_has_one_record_per_image(tmp_path: Path) -> None:
    zip_path = generate_bulk_zip(
        [("sku-1001", 2), ("sku-1002", 1)],
        output_dir=tmp_path,
        include_manifest=True,
        **_SIZE_KWARGS,
    )
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert MANIFEST_FILENAME in names
        manifest_text = zf.read(MANIFEST_FILENAME).decode("utf-8")

    records = [json.loads(line) for line in manifest_text.splitlines()]
    assert len(records) == 3
    assert {record["filename"] for record in records} == {n for n in names if n != MANIFEST_FILENAME}
    for record in records:
        assert record["sku"] in ("SKU-1001", "SKU-1002")
        assert len(record["corners_xy"]) == 4
        assert record["sticker_width"] > 0 and record["sticker_height"] > 0
