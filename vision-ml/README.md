# vision-ml

Notebooks that train a small 3-stage pipeline to read a SKU off one of
`label-api`'s synthetic sticker photos:

1. **Localize** - find the sticker (a rotated rectangle) somewhere in a busy,
   randomly-sized raw photo.
2. **Orient** - given a crop around it, figure out how far off horizontal it
   is.
3. **Read** - given the de-rotated crop, OCR the SKU text.

Each stage is its own small CNN, trained independently against ground truth
(rotation angle + sticker corner points) that `label-api` emits
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
| `LABEL_API_URL` | `http://localhost:8005` | Where the notebooks fetch/generate sticker photos from - start it with `make run-label-api` from the repo root |
| `DATA_DIR` | `data` | Where generated datasets and trained checkpoints land (gitignored) |
| `MLFLOW_TRACKING_URI` | (repo's shared MLflow) | Training notebooks log runs here; leave blank to skip MLflow entirely and just see metrics inline |
| `MLFLOW_WORKSPACE` / `MLFLOW_TRACKING_TOKEN` | - | Same auth pattern as the root `Makefile` - get a token via `oc whoami --show-token` (it expires; refresh it each session) |

`label-api` must be running (`make run-label-api` from
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

## Kubeflow Pipeline

`src/pipeline.py` is a KFP v2 Python DSL pipeline that runs the same
generate -> localize -> orient -> OCR flow as the notebooks above, for
running on OpenShift AI's Data Science Pipelines instead of by hand in
Jupyter. Every notebook hyperparameter (epochs, LR, batch size, pad_frac,
...) is a pipeline input, defaulted to that notebook's current hardcoded
value. All three training stages log to MLflow as nested runs under one
parent run in a single experiment (default `vision-ml`) rather than the
notebooks' one-experiment-per-stage layout, and the pipeline's own output is
that parent run's MLflow URL. Checkpoints are logged both as MLflow
artifacts and as native KFP `Output[Model]` artifacts.

`deploy/Jenkinsfile`'s "vision-ml-trainer" stage builds this `Containerfile`
and pushes it to this repo's shared registry (`global.imageRegistry` in
`deploy/helm/values.yaml`) as both `:$BUILD_NUMBER` and `:latest` - the
`:latest` tag is what `src/pipeline.py`'s `TRAINER_IMAGE` default points at,
so a `pipeline.yaml` compiled once keeps working after later Jenkins builds
without needing to be recompiled/re-imported. To build/push by hand instead
(or point at a pinned build number):

```bash
docker build -t <registry>/vision-ml-trainer:latest -f Containerfile .
docker push <registry>/vision-ml-trainer:latest

# Compile the pipeline, pointing it at that image
VISION_ML_TRAINER_IMAGE=<registry>/vision-ml-trainer:latest python -m src.pipeline
# -> pipeline.yaml
```

Upload `pipeline.yaml` via the Data Science Pipelines UI (Import pipeline),
or `kfp.Client().upload_pipeline`. `mlflow_tracking_auth` defaults to
`kubernetes-namespaced` (same convention as the rest of this repo - see the
root `deploy/helm` chart) so no MLflow token needs to be passed as a run
parameter when the pipeline runs in-cluster; set `mlflow_tracking_token`
instead to override that. `kubernetes-namespaced` only works if the
pipeline's `pipeline-runner-<dspa-name>` service account has been granted
MLflow access -- RHOAI doesn't do this by default the way it does for
workbenches. See "MLflow tracing" in `../deploy/helm/README.md` for the
RoleBinding that grants it.

## Layout

```
src/
  settings.py    # env vars (LABEL_API_URL, DATA_DIR, MLflow)
  client.py       # label-api HTTP client (single fetch + bulk dataset download)
  geometry.py      # corners <-> bbox <-> angle math; crop/pad/derotate - shared by
                   # training-target construction and inference-time reconstruction
  datasets.py       # PyTorch Datasets for the 3 stages, all reading the same manifest.jsonl
  models.py          # LocalizerNet, OrientationNet, CRNNReader + OCR charset/decode
  tracking.py         # MLflow configure helper (no-ops if MLFLOW_TRACKING_URI unset)
  inference.py         # SkuExtractionPipeline - chains the 3 trained models
  pipeline.py           # KFP v2 pipeline: generate + train all 3 stages on OpenShift AI
Containerfile            # Training image src/pipeline.py's components run in
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
