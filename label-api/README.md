# Label API

A small demo service with two, mostly independent, halves:

- A **sticker photo generator**: synthesizes low-quality, camera-style
  photos of white rectangular stickers printed with a SKU - the kind of
  image a warehouse robot's camera might capture of a label stuck to a
  shelf or box. Everything is procedural (PIL/numpy compositing, noise,
  blur, JPEG artifacts); no ML model or real photo is involved.
- A **SKU inference endpoint**: given one of those (or any similar) sticker
  photos, runs it through the 3-stage localize -> orient -> OCR pipeline the
  `vision-ml` project trains, entirely in-process against checkpoints
  bundled into this service's own `models/` directory, and returns the
  predicted SKU plus a confidence score. See "SKU inference" below.

Every generated image:

- Is a plain white rectangle with the SKU printed on it in all caps
- Is placed on a randomly sized canvas - output dimensions are never fixed
- Sits at a random rotation that is never perfectly horizontal (always
  visibly angled)
- Is rendered in color or black-and-white (random by default, or forced)
- Is degraded with grain, blur, and a downsample/upsample pass to read as a
  cheap camera shot rather than a clean render

## Setup

```bash
cd label-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# for running tests
pip install -r requirements-dev.txt
```

`POST /infer` needs the 3 trained checkpoints in `models/` (`localizer.pt`,
`orientation.pt`, `ocr.pt`) - see "SKU inference" below for where those come
from. The sticker-generation endpoints work fine without them.

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
| `INFERENCE_MODELS_DIR` | `models` | Folder containing `localizer.pt`, `orientation.pt`, `ocr.pt` - see "SKU inference" below |
| `INFERENCE_DEVICE` | `cpu` | PyTorch device `POST /infer` runs on |
| `INFERENCE_PAD_FRAC` | `0.2` | Padding added around the localizer's bbox before cropping (fraction of the bbox's longer side) - must match what `vision-ml` trained with |

## REST API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/stickers/{sku}?color_mode=&image_format=` | Generate and stream one sticker photo for `sku` |
| `POST` | `/stickers/bulk` | Generate a batch of sticker photos and return them as a zip |
| `POST` | `/infer` | Multipart image upload -> predicted SKU + confidence, see "SKU inference" below |

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
  "image_format": "jpg",
  "include_manifest": true
}' -o stickers.zip
```

Each request writes its images to a new `BULK_OUTPUT_DIR/batch-<uuid>/`
folder, zips that folder to `BULK_OUTPUT_DIR/batch-<uuid>.zip`, and returns
the zip. The staged folder and zip are left on disk for inspection unless
`BULK_CLEANUP_AFTER_ZIP=true`, in which case only the zip remains.

If `include_manifest` is true (default `false`), the zip also contains a
`manifest.jsonl` with one JSON record per image - `filename`, `sku`,
`canvas_width`/`canvas_height`, `color_mode`, `rotation_angle_degrees`,
`sticker_width`/`sticker_height`, and `corners_xy` (the 4 corners of the
sticker rectangle in that image's pixel coordinates). This is ground truth
for training models against this generator's output (e.g. the `vision-ml`
project's sticker localization/orientation/OCR notebooks) - not needed if
you just want the pictures.

### SKU inference

```bash
curl -X POST http://localhost:8005/infer -F "image=@sticker.jpg" | python3 -m json.tool
```

```json
{
  "sku": "SKU-1001",
  "confidence": 0.993,
  "bbox": [35.5, 253.5, 187.9, 522.3],
  "angle_degrees": 260.3,
  "inference_ms": 22.1
}
```

`POST /infer` takes one multipart-form image (`jpg`/`png`, any size - a
`label-api`-generated sticker photo or anything shaped like one) and reads
the SKU printed on it. [`src/inference.py`](src/inference.py) chains the 3
small PyTorch models the `vision-ml` project trains against this service's
own sticker photos:

1. **Localize** - find the sticker rectangle in the raw photo.
2. **Orient** - figure out how far off horizontal it is.
3. **Read** - de-rotate and OCR the SKU text (CTC greedy decode).

All 3 checkpoints (`models/localizer.pt`, `orientation.pt`, `ocr.pt`) are
loaded into memory once, the first time `/infer` is called, and reused for
every request after that - inference runs entirely in this process on
`INFERENCE_DEVICE` (CPU by default); it never calls out to a separate
inference service or model server. `confidence` is the mean softmax
probability of the OCR model's chosen character at each decoded position
(0.0 for an empty/failed read); `bbox` and `angle_degrees` are the
localizer's and orientation model's intermediate outputs, handy for
debugging a wrong or low-confidence read. Every stage logs its intermediate
result at `DEBUG` (bbox, crop, angle) and the final prediction at `INFO`
(see `LOG_LEVEL`) - set `LOG_LEVEL=DEBUG` to see exactly where a bad
prediction went wrong (localizer, orientation, or OCR).

If a checkpoint is missing from `INFERENCE_MODELS_DIR`, `/infer` returns
`503` rather than crashing the process; regenerate them by running
`vision-ml`'s `01_sticker_localization.ipynb` / `02_sticker_orientation.ipynb`
/ `03_sku_ocr.ipynb` (or its `src/pipeline.py` Kubeflow pipeline) and copying
`vision-ml/data/models/*.pt` into `label-api/models/`.

## MCP

The service also exposes an MCP server (Streamable HTTP transport) at
`http://localhost:8005/mcp`, in the same process as the REST API, sharing the
same loaded models. This is what lets `local-dc-agent`'s fulfillment loop
call SKU inference as an explicit tool during an agentic run, rather than
this service being an implementation detail hidden behind hardcoded
orchestration code.

| Tool | Args | Description |
|---|---|---|
| `infer_sku` | `image_base64: str` | Same prediction as `POST /infer`, for a base64-encoded image instead of a multipart upload. Returns `{sku, confidence, bbox, angle_degrees, inference_ms}` |

The expected caller is `local-inventory-robot-api`'s `get_item_photo` MCP
tool, which returns a sticker photo the same way (base64) - see that
service's README. `local-dc-agent`'s fulfillment policy chains the two:
capture a photo of what was actually picked, then `infer_sku` it, to catch a
mispick or a mislabeled shelf before shipping rather than after.

Connect with any MCP client that supports Streamable HTTP, e.g. the `mcp`
Python SDK's `mcp.client.streamable_http.streamable_http_client`.

## Tests

```bash
pytest
```

Covers the sticker generator (sizing, all-caps text, rotation never landing
on horizontal, color/bw), the bulk zip packaging, the REST API, SKU
inference (`tests/test_inference.py` - runs real predictions against the
bundled checkpoints in `models/`, not mocks, plus the invalid-image and
missing-models error paths), and the `infer_sku` MCP tool
(`tests/test_mcp_server.py`).
