"""Kubeflow Pipelines (KFP v2 Python DSL) definition that trains the same
3-stage SKU extraction pipeline as `notebooks/00-03` - sticker localization,
orientation, and OCR - as one pipeline runnable on OpenShift AI's Data
Science Pipelines instead of by hand in Jupyter.

Each hyperparameter that is a bare literal in the notebooks (epochs, LR,
batch size, pad_frac, ...) is a pipeline input here, defaulted to that exact
literal - see the notebook cell noted in each comment below. Dataset
generation (`00_generate_dataset.ipynb`) is folded in as the pipeline's first
step rather than assumed to already exist, since a KFP run has no access to
a hand-run notebook's `data/raw/` - its generation parameters are inputs too.

Design differences from the notebooks (deliberate, not oversights):

- The notebooks log each stage to its own MLflow experiment
  (`vision-ml/<stage>`, see `tracking.configure_tracking`). This pipeline
  logs all three stages as nested child runs under one parent run in a
  single experiment (`mlflow_experiment_name`, default "vision-ml") instead,
  so the pipeline has exactly one MLflow URL to report as its output -
  that's what `finalize_pipeline_run` returns. `tracking.configure_tracking`
  itself is therefore *not* reused by the training components below.
- Training device selection is CUDA-or-CPU here, not MPS-or-CPU (the
  notebooks target Apple Silicon; KFP pods don't have an MPS device). Unlike
  in `03_sku_ocr.ipynb`, the OCR stage is not force-pinned to CPU - that
  pin was working around `nn.CTCLoss` having no *MPS* kernel, which doesn't
  apply to CUDA.
- Checkpoints are both an MLflow artifact (`mlflow.log_artifact`, alongside
  the run's params/metrics) and a native KFP `Output[Model]` artifact (so
  they show up in the KFP UI's own lineage/artifacts view too) - "store
  artifacts with these experiments as well as kfp artifacts" in the task
  this pipeline was written for.

Usage:

    pip install -r requirements-dev.txt   # for `kfp` itself, compile-time only
    python -m src.pipeline                # writes pipeline.yaml next to this file

Upload the resulting `pipeline.yaml` via OpenShift AI's Data Science
Pipelines UI (Import pipeline), or `kfp` CLI / `kfp.Client().upload_pipeline`.
`TRAINER_IMAGE` below must point at an image built from this project's
`Containerfile` and pushed somewhere the cluster can pull it from - build
and push that yourself; this module only compiles the pipeline definition.
"""

import csv
import os
from pathlib import Path
from typing import NamedTuple

from kfp import compiler, dsl
from kfp.dsl import Dataset, Input, Metrics, Model, Output

# Image built from this project's own Containerfile (installs requirements.txt
# and copies src/ - see that file). Override at compile time, e.g.:
#   VISION_ML_TRAINER_IMAGE=quay.io/you/vision-ml-trainer:1.2.3 python -m src.pipeline
TRAINER_IMAGE = os.environ.get(
    "VISION_ML_TRAINER_IMAGE",
    "image-registry.openshift-image-registry.svc:5000/distribution-center/vision-ml-trainer:latest",
)


def _default_catalog_skus() -> list[str]:
    """The real 20-item product catalog (`products.csv` at the repo root, one
    level above `vision-ml/`) - same file `00_generate_dataset.ipynb` reads to
    build `catalog_skus`. Baked in as this input's default at *compile* time
    so the pipeline's default behavior matches the notebooks' without needing
    the compiling machine's checkout to be reachable at pipeline *run* time."""
    products_csv = Path(__file__).resolve().parent.parent.parent / "products.csv"
    if not products_csv.is_file():
        return []
    with open(products_csv) as f:
        return [row["sku"] for row in csv.DictReader(f)]


DEFAULT_CATALOG_SKUS = _default_catalog_skus()


# ---------------------------------------------------------------------------
# Stage 0: dataset generation (00_generate_dataset.ipynb)
# ---------------------------------------------------------------------------


@dsl.component(base_image=TRAINER_IMAGE)
def generate_dataset(
    label_generator_api_url: str,
    catalog_skus: list[str],
    catalog_qty_per_sku: int,
    synthetic_num_skus: int,
    synthetic_qty_per_sku: int,
    val_frac: float,
    test_frac: float,
    split_seed: int,
    raw_data: Output[Dataset],
) -> None:
    import json
    import os
    import random
    from pathlib import Path

    os.environ["LABEL_GENERATOR_API_URL"] = label_generator_api_url
    from src import client, datasets  # noqa: E402 - needs LABEL_GENERATOR_API_URL set first

    random.seed(split_seed)
    dest_dir = Path(raw_data.path)

    # notebook cell: catalog_numbers/candidate_numbers/synthetic_skus/items
    catalog_numbers = {int(sku.split("-")[1]) for sku in catalog_skus}
    candidate_numbers = [n for n in range(1000, 10000) if n not in catalog_numbers]
    synthetic_numbers = random.sample(candidate_numbers, synthetic_num_skus)
    synthetic_skus = [f"SKU-{n}" for n in synthetic_numbers]

    items = [(sku, catalog_qty_per_sku) for sku in catalog_skus] + [
        (sku, synthetic_qty_per_sku) for sku in synthetic_skus
    ]

    manifest_path = client.download_dataset(items, dest_dir=dest_dir)
    records = datasets.load_manifest(manifest_path)

    train_records, val_records, test_records = datasets.split_records(
        records, val_frac=val_frac, test_frac=test_frac, seed=split_seed, held_out_skus=catalog_skus,
    )
    splits = {
        "train_skus": sorted({r["sku"] for r in train_records}),
        "val_skus": sorted({r["sku"] for r in val_records}),
        "test_skus": sorted({r["sku"] for r in test_records}),
        "catalog_skus": catalog_skus,
    }
    (dest_dir / "splits.json").write_text(json.dumps(splits, indent=2))
    print(f"generated {len(records)} images -> {dest_dir}")


# ---------------------------------------------------------------------------
# MLflow parent run (all 3 stages log as nested children under this one run)
# ---------------------------------------------------------------------------


@dsl.component(base_image=TRAINER_IMAGE)
def start_pipeline_run(
    mlflow_tracking_uri: str,
    mlflow_workspace: str,
    mlflow_tracking_token: str,
    mlflow_tracking_auth: str,
    mlflow_experiment_name: str,
    run_name: str,
) -> NamedTuple("Outputs", [("run_id", str), ("experiment_id", str)]):  # noqa: F821
    from collections import namedtuple

    import mlflow

    from src import tracking

    tracking.configure_mlflow_env(mlflow_tracking_uri, mlflow_workspace, mlflow_tracking_token, mlflow_tracking_auth)
    mlflow.set_tracking_uri(mlflow_tracking_uri)
    experiment_id = mlflow.set_experiment(mlflow_experiment_name).experiment_id
    # Left RUNNING on purpose - each stage's component is a separate process/pod
    # and attaches to this run by id+tag rather than by staying inside a `with`
    # block here. finalize_pipeline_run marks it FINISHED once all 3 are done.
    run = mlflow.start_run(run_name=run_name, experiment_id=experiment_id)

    Outputs = namedtuple("Outputs", ["run_id", "experiment_id"])
    return Outputs(run.info.run_id, experiment_id)


# ---------------------------------------------------------------------------
# Stage 1: sticker localization (01_sticker_localization.ipynb)
# ---------------------------------------------------------------------------


@dsl.component(base_image=TRAINER_IMAGE)
def train_localizer(
    raw_data: Input[Dataset],
    mlflow_tracking_uri: str,
    mlflow_workspace: str,
    mlflow_tracking_token: str,
    mlflow_tracking_auth: str,
    mlflow_experiment_id: str,
    parent_run_id: str,
    epochs: int,
    lr: float,
    batch_size: int,
    random_seed: int,
    checkpoint: Output[Model],
    metrics: Output[Metrics],
) -> NamedTuple("Outputs", [("run_id", str), ("best_val_iou", float)]):  # noqa: F821
    import json
    import random
    from collections import namedtuple
    from pathlib import Path

    import mlflow
    import torch
    from torch import nn
    from torch.utils.data import DataLoader

    from src import datasets, models, tracking

    tracking.configure_mlflow_env(mlflow_tracking_uri, mlflow_workspace, mlflow_tracking_token, mlflow_tracking_auth)
    mlflow.set_tracking_uri(mlflow_tracking_uri)

    random.seed(random_seed)
    torch.manual_seed(random_seed)
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

    raw_dir = Path(raw_data.path)
    records = datasets.load_manifest(raw_dir / "manifest.jsonl")
    splits = json.loads((raw_dir / "splits.json").read_text())

    def _records_for(skus):
        sku_set = set(skus)
        return [r for r in records if r["sku"] in sku_set]

    train_records = _records_for(splits["train_skus"])
    val_records = _records_for(splits["val_skus"])
    print(f"train: {len(train_records)} images   val: {len(val_records)} images")

    train_ds = datasets.LocalizationDataset(train_records, raw_dir)
    val_ds = datasets.LocalizationDataset(val_records, raw_dir)
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)

    def batch_iou(pred: torch.Tensor, target: torch.Tensor) -> float:
        px0, py0, px1, py1 = pred.unbind(-1)
        tx0, ty0, tx1, ty1 = target.unbind(-1)
        ix0, iy0 = torch.maximum(px0, tx0), torch.maximum(py0, ty0)
        ix1, iy1 = torch.minimum(px1, tx1), torch.minimum(py1, ty1)
        intersection = (ix1 - ix0).clamp(min=0) * (iy1 - iy0).clamp(min=0)
        pred_area = (px1 - px0).clamp(min=0) * (py1 - py0).clamp(min=0)
        target_area = (tx1 - tx0).clamp(min=0) * (ty1 - ty0).clamp(min=0)
        union = (pred_area + target_area - intersection).clamp(min=1e-6)
        return (intersection / union).mean().item()

    model = models.LocalizerNet().to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.SmoothL1Loss()

    run = mlflow.start_run(
        experiment_id=mlflow_experiment_id,
        run_name="localizer",
        tags={"mlflow.parentRunId": parent_run_id},
    )

    best_val_iou = -1.0
    best_state_dict = None
    with run:
        mlflow.log_params(
            {"stage": "localizer", "epochs": epochs, "batch_size": batch_size, "lr": lr,
             "model": "LocalizerNet", "random_seed": random_seed}
        )

        for epoch in range(epochs):
            model.train()
            train_loss_total = 0.0
            for images, targets in train_loader:
                images, targets = images.to(DEVICE), targets.to(DEVICE)
                optimizer.zero_grad()
                preds = model(images)
                loss = loss_fn(preds, targets)
                loss.backward()
                optimizer.step()
                train_loss_total += loss.item() * images.size(0)
            train_loss = train_loss_total / len(train_ds)

            model.eval()
            val_loss_total = 0.0
            val_iou_total = 0.0
            with torch.no_grad():
                for images, targets in val_loader:
                    images, targets = images.to(DEVICE), targets.to(DEVICE)
                    preds = model(images)
                    val_loss_total += loss_fn(preds, targets).item() * images.size(0)
                    val_iou_total += batch_iou(preds, targets) * images.size(0)
            val_loss = val_loss_total / len(val_ds)
            val_iou = val_iou_total / len(val_ds)

            is_best = val_iou > best_val_iou
            if is_best:
                best_val_iou = val_iou
                best_state_dict = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            print(f"epoch {epoch + 1:2d}/{epochs}  train_loss={train_loss:.4f}  val_loss={val_loss:.4f}  "
                  f"val_iou={val_iou:.3f}{'  (best)' if is_best else ''}")

            mlflow.log_metrics({"train_loss": train_loss, "val_loss": val_loss, "val_iou": val_iou}, step=epoch)

        model.load_state_dict(best_state_dict)
        torch.save(model.state_dict(), checkpoint.path)
        mlflow.log_metric("best_val_iou", best_val_iou)
        mlflow.log_artifact(checkpoint.path, artifact_path="checkpoints")
        run_id = mlflow.active_run().info.run_id

    metrics.log_metric("best_val_iou", best_val_iou)
    print(f"saved checkpoint (best val_iou={best_val_iou:.3f})")

    Outputs = namedtuple("Outputs", ["run_id", "best_val_iou"])
    return Outputs(run_id, best_val_iou)


# ---------------------------------------------------------------------------
# Stage 2: sticker orientation (02_sticker_orientation.ipynb)
# ---------------------------------------------------------------------------


@dsl.component(base_image=TRAINER_IMAGE)
def train_orientation(
    raw_data: Input[Dataset],
    mlflow_tracking_uri: str,
    mlflow_workspace: str,
    mlflow_tracking_token: str,
    mlflow_tracking_auth: str,
    mlflow_experiment_id: str,
    parent_run_id: str,
    epochs: int,
    lr: float,
    batch_size: int,
    pad_frac: float,
    random_seed: int,
    checkpoint: Output[Model],
    metrics: Output[Metrics],
) -> NamedTuple("Outputs", [("run_id", str), ("best_val_angle_error_deg", float)]):  # noqa: F821
    import json
    import math
    import random
    from collections import namedtuple
    from pathlib import Path

    import mlflow
    import torch
    from torch import nn
    from torch.utils.data import DataLoader

    from src import datasets, models, tracking

    tracking.configure_mlflow_env(mlflow_tracking_uri, mlflow_workspace, mlflow_tracking_token, mlflow_tracking_auth)
    mlflow.set_tracking_uri(mlflow_tracking_uri)

    random.seed(random_seed)
    torch.manual_seed(random_seed)
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

    raw_dir = Path(raw_data.path)
    records = datasets.load_manifest(raw_dir / "manifest.jsonl")
    splits = json.loads((raw_dir / "splits.json").read_text())

    def _records_for(skus):
        sku_set = set(skus)
        return [r for r in records if r["sku"] in sku_set]

    train_records = _records_for(splits["train_skus"])
    val_records = _records_for(splits["val_skus"])
    print(f"train: {len(train_records)} images   val: {len(val_records)} images")

    train_ds = datasets.OrientationDataset(train_records, raw_dir, pad_frac=pad_frac)
    val_ds = datasets.OrientationDataset(val_records, raw_dir, pad_frac=pad_frac)
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)

    def angular_error_degrees(pred: torch.Tensor, target: torch.Tensor) -> float:
        pred_angle = torch.atan2(pred[:, 0], pred[:, 1])
        target_angle = torch.atan2(target[:, 0], target[:, 1])
        diff = torch.atan2(torch.sin(pred_angle - target_angle), torch.cos(pred_angle - target_angle))
        return diff.abs().mean().item() * 180 / math.pi

    model = models.OrientationNet().to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()

    run = mlflow.start_run(
        experiment_id=mlflow_experiment_id,
        run_name="orientation",
        tags={"mlflow.parentRunId": parent_run_id},
    )

    best_val_angle_error = float("inf")
    best_state_dict = None
    with run:
        mlflow.log_params(
            {"stage": "orientation", "epochs": epochs, "batch_size": batch_size, "lr": lr,
             "pad_frac": pad_frac, "model": "OrientationNet", "random_seed": random_seed}
        )

        for epoch in range(epochs):
            model.train()
            train_loss_total = 0.0
            for images, targets in train_loader:
                images, targets = images.to(DEVICE), targets.to(DEVICE)
                optimizer.zero_grad()
                preds = model(images)
                loss = loss_fn(preds, targets)
                loss.backward()
                optimizer.step()
                train_loss_total += loss.item() * images.size(0)
            train_loss = train_loss_total / len(train_ds)

            model.eval()
            val_loss_total = 0.0
            val_angle_total = 0.0
            with torch.no_grad():
                for images, targets in val_loader:
                    images, targets = images.to(DEVICE), targets.to(DEVICE)
                    preds = model(images)
                    val_loss_total += loss_fn(preds, targets).item() * images.size(0)
                    val_angle_total += angular_error_degrees(preds, targets) * images.size(0)
            val_loss = val_loss_total / len(val_ds)
            val_angle_error = val_angle_total / len(val_ds)

            is_best = val_angle_error < best_val_angle_error
            if is_best:
                best_val_angle_error = val_angle_error
                best_state_dict = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            print(f"epoch {epoch + 1:2d}/{epochs}  train_loss={train_loss:.4f}  val_loss={val_loss:.4f}  "
                  f"val_angle_error={val_angle_error:.2f}deg{'  (best)' if is_best else ''}")

            mlflow.log_metrics(
                {"train_loss": train_loss, "val_loss": val_loss, "val_angle_error_deg": val_angle_error}, step=epoch
            )

        model.load_state_dict(best_state_dict)
        torch.save(model.state_dict(), checkpoint.path)
        mlflow.log_metric("best_val_angle_error_deg", best_val_angle_error)
        mlflow.log_artifact(checkpoint.path, artifact_path="checkpoints")
        run_id = mlflow.active_run().info.run_id

    metrics.log_metric("best_val_angle_error_deg", best_val_angle_error)
    print(f"saved checkpoint (best val_angle_error={best_val_angle_error:.2f}deg)")

    Outputs = namedtuple("Outputs", ["run_id", "best_val_angle_error_deg"])
    return Outputs(run_id, best_val_angle_error)


# ---------------------------------------------------------------------------
# Stage 3: SKU OCR (03_sku_ocr.ipynb)
# ---------------------------------------------------------------------------


@dsl.component(base_image=TRAINER_IMAGE)
def train_ocr(
    raw_data: Input[Dataset],
    mlflow_tracking_uri: str,
    mlflow_workspace: str,
    mlflow_tracking_token: str,
    mlflow_tracking_auth: str,
    mlflow_experiment_id: str,
    parent_run_id: str,
    epochs: int,
    lr: float,
    batch_size: int,
    pad_frac: float,
    angle_jitter_std: float,
    rnn_hidden: int,
    random_seed: int,
    checkpoint: Output[Model],
    metrics: Output[Metrics],
) -> NamedTuple("Outputs", [("run_id", str), ("best_val_exact_match_accuracy", float)]):  # noqa: F821
    import json
    import random
    from collections import namedtuple
    from pathlib import Path

    import mlflow
    import torch
    from torch.utils.data import DataLoader

    from src import datasets, models, tracking

    tracking.configure_mlflow_env(mlflow_tracking_uri, mlflow_workspace, mlflow_tracking_token, mlflow_tracking_auth)
    mlflow.set_tracking_uri(mlflow_tracking_uri)

    random.seed(random_seed)
    torch.manual_seed(random_seed)
    # Unlike the notebook (pinned to CPU: nn.CTCLoss has no *MPS* kernel), CUDA
    # supports CTCLoss fine, so this stage uses the GPU too when one's available.
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

    raw_dir = Path(raw_data.path)
    records = datasets.load_manifest(raw_dir / "manifest.jsonl")
    splits = json.loads((raw_dir / "splits.json").read_text())

    def _records_for(skus):
        sku_set = set(skus)
        return [r for r in records if r["sku"] in sku_set]

    train_records = _records_for(splits["train_skus"])
    val_records = _records_for(splits["val_skus"])
    catalog_records = _records_for(splits["catalog_skus"])
    print(f"train: {len(train_records)} images, {len(splits['train_skus'])} SKUs")
    print(f"val:   {len(val_records)} images, {len(splits['val_skus'])} SKUs")
    print(f"catalog (held out, real product SKUs): {len(catalog_records)} images, {len(splits['catalog_skus'])} SKUs")

    def _make_ocr_dataset(records):
        # angle_jitter_std defaults to 0.0, not OcrDataset's own default of 8.0
        # - see 03_sku_ocr.ipynb's cell 4 comment: rotation jitter broke this
        # architecture's training (unlike OrientationNet's global average
        # pooling, the CRNN reads left-to-right through vertical column slices).
        return datasets.OcrDataset(records, raw_dir, pad_frac=pad_frac, angle_jitter_std=angle_jitter_std)

    train_ds = _make_ocr_dataset(train_records)
    val_ds = _make_ocr_dataset(val_records)
    catalog_ds = _make_ocr_dataset(catalog_records)

    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, collate_fn=datasets.ocr_collate)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, collate_fn=datasets.ocr_collate)
    catalog_loader = DataLoader(catalog_ds, batch_size=batch_size, shuffle=False, collate_fn=datasets.ocr_collate)

    def _levenshtein(a: str, b: str) -> int:
        if len(a) < len(b):
            a, b = b, a
        previous = list(range(len(b) + 1))
        for i, ca in enumerate(a, start=1):
            current = [i] + [0] * len(b)
            for j, cb in enumerate(b, start=1):
                cost = 0 if ca == cb else 1
                current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            previous = current
        return previous[-1]

    @torch.no_grad()
    def evaluate(model, loader) -> dict:
        model.eval()
        total_chars = 0
        total_edits = 0
        exact_matches = 0
        total = 0
        for images, _targets, _lengths, skus in loader:
            images = images.to(DEVICE)
            log_probs = model(images)
            preds = models.greedy_ctc_decode(log_probs)
            for pred, truth in zip(preds, skus):
                total_edits += _levenshtein(pred, truth)
                total_chars += len(truth)
                exact_matches += int(pred == truth)
                total += 1
        return {
            "cer": total_edits / max(total_chars, 1),
            "exact_match_accuracy": exact_matches / max(total, 1),
        }

    model = models.CRNNReader(rnn_hidden=rnn_hidden).to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    ctc_loss = torch.nn.CTCLoss(blank=models.BLANK_IDX, zero_infinity=True)

    run = mlflow.start_run(
        experiment_id=mlflow_experiment_id,
        run_name="ocr",
        tags={"mlflow.parentRunId": parent_run_id},
    )

    best_val_exact_match = -1.0
    best_state_dict = None
    with run:
        mlflow.log_params(
            {"stage": "ocr", "epochs": epochs, "batch_size": batch_size, "lr": lr, "pad_frac": pad_frac,
             "angle_jitter_std": angle_jitter_std, "rnn_hidden": rnn_hidden, "model": "CRNNReader",
             "random_seed": random_seed}
        )

        for epoch in range(epochs):
            model.train()
            train_loss_total = 0.0
            n_images = 0
            for images, targets, target_lengths, _skus in train_loader:
                images, targets, target_lengths = images.to(DEVICE), targets.to(DEVICE), target_lengths.to(DEVICE)
                optimizer.zero_grad()
                log_probs = model(images)  # (T, B, C)
                input_lengths = torch.full((images.size(0),), log_probs.size(0), dtype=torch.long, device=DEVICE)
                loss = ctc_loss(log_probs, targets, input_lengths, target_lengths)
                loss.backward()
                optimizer.step()
                train_loss_total += loss.item() * images.size(0)
                n_images += images.size(0)
            train_loss = train_loss_total / n_images

            val_metrics = evaluate(model, val_loader)
            is_best = val_metrics["exact_match_accuracy"] > best_val_exact_match
            if is_best:
                best_val_exact_match = val_metrics["exact_match_accuracy"]
                best_state_dict = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            print(f"epoch {epoch + 1:2d}/{epochs}  train_loss={train_loss:.4f}  "
                  f"val_cer={val_metrics['cer']:.3f}  val_exact_match={val_metrics['exact_match_accuracy']:.3f}"
                  f"{'  (best)' if is_best else ''}")

            mlflow.log_metrics({"train_loss": train_loss, **{f"val_{k}": v for k, v in val_metrics.items()}}, step=epoch)

        model.load_state_dict(best_state_dict)
        torch.save(model.state_dict(), checkpoint.path)
        mlflow.log_metric("best_val_exact_match_accuracy", best_val_exact_match)

        # Bonus eval from 03_sku_ocr.ipynb cell 11: accuracy on the real product
        # catalog, which no split ever trains on - the number that actually
        # matters for this stage, since the synthetic val split is easier.
        catalog_metrics = evaluate(model, catalog_loader)
        mlflow.log_metrics({f"catalog_{k}": v for k, v in catalog_metrics.items()})
        print("real catalog (never trained on):", catalog_metrics)

        mlflow.log_artifact(checkpoint.path, artifact_path="checkpoints")
        run_id = mlflow.active_run().info.run_id

    metrics.log_metric("best_val_exact_match_accuracy", best_val_exact_match)
    metrics.log_metric("catalog_exact_match_accuracy", catalog_metrics["exact_match_accuracy"])
    print(f"saved checkpoint (best val_exact_match={best_val_exact_match:.3f})")

    Outputs = namedtuple("Outputs", ["run_id", "best_val_exact_match_accuracy"])
    return Outputs(run_id, best_val_exact_match)


# ---------------------------------------------------------------------------
# Finalize: close out the parent MLflow run, report its URL as the pipeline's
# own output ("the url to the specific mlflow experiment with the artifacts")
# ---------------------------------------------------------------------------


@dsl.component(base_image=TRAINER_IMAGE)
def finalize_pipeline_run(
    mlflow_tracking_uri: str,
    mlflow_workspace: str,
    mlflow_tracking_token: str,
    mlflow_tracking_auth: str,
    experiment_id: str,
    parent_run_id: str,
    localizer_run_id: str,
    localizer_best_val_iou: float,
    orientation_run_id: str,
    orientation_best_val_angle_error_deg: float,
    ocr_run_id: str,
    ocr_best_val_exact_match_accuracy: float,
) -> str:
    from mlflow.tracking import MlflowClient

    from src import tracking

    tracking.configure_mlflow_env(mlflow_tracking_uri, mlflow_workspace, mlflow_tracking_token, mlflow_tracking_auth)
    client = MlflowClient(tracking_uri=mlflow_tracking_uri)

    for key, value in {
        "localizer_best_val_iou": localizer_best_val_iou,
        "orientation_best_val_angle_error_deg": orientation_best_val_angle_error_deg,
        "ocr_best_val_exact_match_accuracy": ocr_best_val_exact_match_accuracy,
    }.items():
        client.log_metric(parent_run_id, key, value)

    for key, value in {
        "localizer_run_id": localizer_run_id,
        "orientation_run_id": orientation_run_id,
        "ocr_run_id": ocr_run_id,
    }.items():
        client.set_tag(parent_run_id, key, value)

    client.set_terminated(parent_run_id, status="FINISHED")

    url = f"{mlflow_tracking_uri.rstrip('/')}/#/experiments/{experiment_id}/runs/{parent_run_id}"
    print(f"MLflow run: {url}")
    return url


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@dsl.pipeline(
    name="vision-ml-training",
    description="Trains the 3-stage SKU sticker extraction pipeline (localize, orient, OCR) and logs all three as nested MLflow runs under one parent run.",
)
def vision_ml_training_pipeline(
    # Dataset generation - 00_generate_dataset.ipynb
    label_generator_api_url: str = "http://localhost:8005",
    catalog_skus: list[str] = DEFAULT_CATALOG_SKUS,
    catalog_qty_per_sku: int = 40,
    synthetic_num_skus: int = 800,
    synthetic_qty_per_sku: int = 8,
    val_frac: float = 0.1,
    test_frac: float = 0.05,
    split_seed: int = 0,
    random_seed: int = 0,
    # Stage 1: localization - 01_sticker_localization.ipynb
    localizer_epochs: int = 15,
    localizer_lr: float = 1e-3,
    localizer_batch_size: int = 32,
    # Stage 2: orientation - 02_sticker_orientation.ipynb
    orientation_epochs: int = 30,
    orientation_lr: float = 1e-3,
    orientation_batch_size: int = 32,
    orientation_pad_frac: float = 0.2,
    # Stage 3: OCR - 03_sku_ocr.ipynb
    ocr_epochs: int = 40,
    ocr_lr: float = 1e-3,
    ocr_batch_size: int = 32,
    ocr_pad_frac: float = 0.2,
    ocr_angle_jitter_std: float = 0.0,
    ocr_rnn_hidden: int = 128,
    # MLflow - same env vars/defaults as vision-ml/.env.example and the root Makefile
    mlflow_tracking_uri: str = "https://rh-ai.apps.ocp.home.glroland.com/mlflow",
    mlflow_workspace: str = "distribution-center",
    mlflow_experiment_name: str = "vision-ml",
    mlflow_tracking_token: str = "",
    mlflow_tracking_auth: str = "kubernetes-namespaced",
) -> str:
    dataset = generate_dataset(
        label_generator_api_url=label_generator_api_url,
        catalog_skus=catalog_skus,
        catalog_qty_per_sku=catalog_qty_per_sku,
        synthetic_num_skus=synthetic_num_skus,
        synthetic_qty_per_sku=synthetic_qty_per_sku,
        val_frac=val_frac,
        test_frac=test_frac,
        split_seed=split_seed,
    ).set_caching_options(False)

    parent_run = start_pipeline_run(
        mlflow_tracking_uri=mlflow_tracking_uri,
        mlflow_workspace=mlflow_workspace,
        mlflow_tracking_token=mlflow_tracking_token,
        mlflow_tracking_auth=mlflow_tracking_auth,
        mlflow_experiment_name=mlflow_experiment_name,
        run_name="vision-ml-training",
    ).set_caching_options(False)

    localizer = train_localizer(
        raw_data=dataset.outputs["raw_data"],
        mlflow_tracking_uri=mlflow_tracking_uri,
        mlflow_workspace=mlflow_workspace,
        mlflow_tracking_token=mlflow_tracking_token,
        mlflow_tracking_auth=mlflow_tracking_auth,
        mlflow_experiment_id=parent_run.outputs["experiment_id"],
        parent_run_id=parent_run.outputs["run_id"],
        epochs=localizer_epochs,
        lr=localizer_lr,
        batch_size=localizer_batch_size,
        random_seed=random_seed,
    ).set_caching_options(False)

    orientation = train_orientation(
        raw_data=dataset.outputs["raw_data"],
        mlflow_tracking_uri=mlflow_tracking_uri,
        mlflow_workspace=mlflow_workspace,
        mlflow_tracking_token=mlflow_tracking_token,
        mlflow_tracking_auth=mlflow_tracking_auth,
        mlflow_experiment_id=parent_run.outputs["experiment_id"],
        parent_run_id=parent_run.outputs["run_id"],
        epochs=orientation_epochs,
        lr=orientation_lr,
        batch_size=orientation_batch_size,
        pad_frac=orientation_pad_frac,
        random_seed=random_seed,
    ).set_caching_options(False)

    ocr = train_ocr(
        raw_data=dataset.outputs["raw_data"],
        mlflow_tracking_uri=mlflow_tracking_uri,
        mlflow_workspace=mlflow_workspace,
        mlflow_tracking_token=mlflow_tracking_token,
        mlflow_tracking_auth=mlflow_tracking_auth,
        mlflow_experiment_id=parent_run.outputs["experiment_id"],
        parent_run_id=parent_run.outputs["run_id"],
        epochs=ocr_epochs,
        lr=ocr_lr,
        batch_size=ocr_batch_size,
        pad_frac=ocr_pad_frac,
        angle_jitter_std=ocr_angle_jitter_std,
        rnn_hidden=ocr_rnn_hidden,
        random_seed=random_seed,
    ).set_caching_options(False)

    final = finalize_pipeline_run(
        mlflow_tracking_uri=mlflow_tracking_uri,
        mlflow_workspace=mlflow_workspace,
        mlflow_tracking_token=mlflow_tracking_token,
        mlflow_tracking_auth=mlflow_tracking_auth,
        experiment_id=parent_run.outputs["experiment_id"],
        parent_run_id=parent_run.outputs["run_id"],
        localizer_run_id=localizer.outputs["run_id"],
        localizer_best_val_iou=localizer.outputs["best_val_iou"],
        orientation_run_id=orientation.outputs["run_id"],
        orientation_best_val_angle_error_deg=orientation.outputs["best_val_angle_error_deg"],
        ocr_run_id=ocr.outputs["run_id"],
        ocr_best_val_exact_match_accuracy=ocr.outputs["best_val_exact_match_accuracy"],
    ).set_caching_options(False)

    return final.output


if __name__ == "__main__":
    output_path = Path(__file__).resolve().parent.parent / "pipeline.yaml"
    compiler.Compiler().compile(vision_ml_training_pipeline, str(output_path))
    print(f"compiled {output_path}")
