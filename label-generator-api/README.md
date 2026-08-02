# Label Generator API

A small demo API that synthesizes low-quality, camera-style photos of white
rectangular stickers printed with a SKU - the kind of image a warehouse
robot's camera might capture of a label stuck to a shelf or box. Everything
is procedural (PIL/numpy compositing, noise, blur, JPEG artifacts); there is
no ML model and no real photo involved.

Every image:

- Is a plain white rectangle with the SKU printed on it in all caps
- Is placed on a randomly sized canvas - output dimensions are never fixed
- Sits at a random rotation that is never perfectly horizontal (always
  visibly angled)
- Is rendered in color or black-and-white (random by default, or forced)
- Is degraded with grain, blur, and a downsample/upsample pass to read as a
  cheap camera shot rather than a clean render

## Setup

```bash
cd label-generator-api
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

Starts the API on `http://0.0.0.0:8005`. Interactive docs are available at
`http://localhost:8005/docs`.

Settings are defined in [`src/settings.py`](src/settings.py) with reasonable
defaults; copy `.env.example` to `.env` and fill it in to override them, or
set environment variables directly (env vars take precedence over `.env`).

| Env var | Default | Description |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `8005` | Bind port |
| `MIN_IMAGE_WIDTH` / `MAX_IMAGE_WIDTH` | `480` / `900` | Random width range for each generated image |
| `MIN_IMAGE_HEIGHT` / `MAX_IMAGE_HEIGHT` | `360` / `700` | Random height range for each generated image |
| `BULK_OUTPUT_DIR` | `output` | Local folder bulk-generated images are staged in (as `batch-<uuid>/` subfolders) before being zipped |
| `BULK_CLEANUP_AFTER_ZIP` | `false` | If `true`, delete each batch's image folder after zipping, leaving only the zip on disk |

## REST API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/stickers/{sku}?color_mode=&image_format=` | Generate and stream one sticker photo for `sku` |
| `POST` | `/stickers/bulk` | Generate a batch of sticker photos and return them as a zip |

`color_mode` is one of `random` (default), `color`, `bw`. `image_format` is
one of `jpg` (default) or `png`.

```bash
curl "http://localhost:8005/stickers/SKU-1001?color_mode=color&image_format=jpg" -o sticker.jpg
```

### Bulk generation

```bash
curl -X POST http://localhost:8005/stickers/bulk -H 'Content-Type: application/json' -d '{
  "items": [
    {"sku": "SKU-1001", "quantity": 3},
    {"sku": "SKU-1002", "quantity": 1}
  ],
  "color_mode": "random",
  "image_format": "jpg"
}' -o stickers.zip
```

Each request writes its images to a new `BULK_OUTPUT_DIR/batch-<uuid>/`
folder, zips that folder to `BULK_OUTPUT_DIR/batch-<uuid>.zip`, and returns
the zip. The staged folder and zip are left on disk for inspection unless
`BULK_CLEANUP_AFTER_ZIP=true`, in which case only the zip remains.

## Tests

```bash
pytest
```

Covers the sticker generator (sizing, all-caps text, rotation never landing
on horizontal, color/bw), the bulk zip packaging, and the REST API.
