# vision-ml

Notebooks that train a small 3-stage pipeline to read a SKU off one of
`label-generator-api`'s synthetic sticker photos:

1. **Localize** - find the sticker (a rotated rectangle) somewhere in a busy,
   randomly-sized raw photo.
2. **Orient** - given a crop around it, figure out how far off horizontal it
   is.
3. **Read** - given the de-rotated crop, OCR the SKU text.

Each stage is its own small CNN, trained independently against ground truth
(rotation angle + sticker corner points) that `label-generator-api` emits
via its bulk endpoint's optional manifest - see that service's README for the
`include_manifest` option this all depends on. A final notebook chains the
three trained models together and evaluates true end-to-end accuracy on the
real 20-item product catalog (`../products.csv`), which none of the three
models ever train on.

## Setup

```bash
cd vision-ml
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# for running tests
pip install -r requirements-dev.txt
```

Copy `.env.example` to `.env` and adjust if needed:

| Env var | Default | Description |
|---|---|---|
| `LABEL_GENERATOR_API_URL` | `http://localhost:8005` | Where the notebooks fetch/generate sticker photos from - start it with `make run-label-generator-api` from the repo root |
| `DATA_DIR` | `data` | Where generated datasets and trained checkpoints land (gitignored) |
| `MLFLOW_TRACKING_URI` | (repo's shared MLflow) | Training notebooks log runs here; leave blank to skip MLflow entirely and just see metrics inline |
| `MLFLOW_WORKSPACE` / `MLFLOW_TRACKING_TOKEN` | - | Same auth pattern as the root `Makefile` - get a token via `oc whoami --show-token` (it expires; refresh it each session) |

`label-generator-api` must be running (`make run-label-generator-api` from
the repo root, or `make start-all`) before running `00_generate_dataset.ipynb`
or `04_end_to_end_pipeline.ipynb`.

## Running the notebooks

```bash
jupyter lab notebooks/
```

Run in order - each later notebook depends on artifacts the earlier ones
produce under `data/`:

| Notebook | Produces |
|---|---|
| `00_generate_dataset.ipynb` | `data/raw/` - generated images + `manifest.jsonl`, train/val/test SKU splits, a visual sanity check of the ground-truth geometry |
| `01_sticker_localization.ipynb` | `data/models/localizer.pt` |
| `02_sticker_orientation.ipynb` | `data/models/orientation.pt` |
| `03_sku_ocr.ipynb` | `data/models/ocr.pt` |
| `04_end_to_end_pipeline.ipynb` | Chains all three, reports live end-to-end SKU accuracy on the real product catalog |

## Layout

```
src/
  settings.py    # env vars (LABEL_GENERATOR_API_URL, DATA_DIR, MLflow)
  client.py       # label-generator-api HTTP client (single fetch + bulk dataset download)
  geometry.py      # corners <-> bbox <-> angle math; crop/pad/derotate - shared by
                   # training-target construction and inference-time reconstruction
  datasets.py       # PyTorch Datasets for the 3 stages, all reading the same manifest.jsonl
  models.py          # LocalizerNet, OrientationNet, CRNNReader + OCR charset/decode
  tracking.py         # MLflow configure helper (no-ops if MLFLOW_TRACKING_URI unset)
  inference.py         # SkuExtractionPipeline - chains the 3 trained models
notebooks/              # 00-04, see table above
data/                    # generated datasets + checkpoints (gitignored)
```

## Tests

```bash
pytest
```

Covers `geometry.py`'s corner/angle/rotation math, including a real-image
check (draws a tilted rectangle with actual `PIL.Image.rotate`, not just
point math, and confirms `rotate_upright` straightens it) - this is the math
every training target and every inference-time crop depends on, so it's
worth trusting before spending time training on top of it.
