"""CLI entrypoint for loading prompts.json into the MLflow Prompt Registry."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

DEFAULT_CATALOG = Path(__file__).resolve().parent.parent / "prompts.json"

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="src",
        description="Register every prompt in prompts.json as a version in the MLflow Prompt Registry.",
    )
    parser.add_argument(
        "--file",
        "-f",
        default=str(DEFAULT_CATALOG),
        help=f"Path to the prompt catalog JSON. Default: {DEFAULT_CATALOG}",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        help="Prompt id to load (repeatable). Default: load every prompt in the catalog.",
    )
    parser.add_argument(
        "--commit-message",
        "-m",
        default=None,
        help="Commit message applied to every version registered in this run. "
        "Default: a message noting the source catalog file.",
    )
    parser.add_argument(
        "--tracking-uri",
        default=None,
        help="Override MLFLOW_TRACKING_URI for this run instead of reading it from the environment.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without writing to MLflow. Still reads the current "
        "latest version of each prompt (if MLFLOW_TRACKING_URI is set) to predict "
        "create/update/skip accurately; falls back to an offline listing otherwise.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Register a new version even if it's identical to the current latest version.",
    )
    return parser.parse_args(argv)


def load_catalog(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    prompts = data["prompts"]
    if not prompts:
        raise ValueError(f"No prompts found in {path}")
    return prompts


def _tags_for(prompt: dict[str, Any]) -> dict[str, str]:
    tags = {
        "service": prompt["service"],
        "source_file": prompt["source_file"],
        "source_symbol": prompt["source_symbol"],
        "role": prompt["role"],
    }
    if prompt.get("variables"):
        tags["variables"] = ",".join(prompt["variables"])
    return tags


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"))
    args = parse_args(argv)

    catalog_path = Path(args.file)
    prompts = load_catalog(catalog_path)
    if args.only:
        wanted = set(args.only)
        prompts = [p for p in prompts if p["id"] in wanted]
        missing = wanted - {p["id"] for p in prompts}
        if missing:
            print(f"error: unknown prompt id(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 1

    commit_message = args.commit_message or f"Loaded from {catalog_path.name}"

    tracking_uri = args.tracking_uri or os.environ.get("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        if args.dry_run:
            for prompt in prompts:
                print(f"[dry-run] would check '{prompt['id']}' ({len(prompt['template'])} chars) -- offline, no MLFLOW_TRACKING_URI set")
            return 0
        print(
            "error: no MLflow tracking URI configured. Set MLFLOW_TRACKING_URI "
            "(and MLFLOW_WORKSPACE/MLFLOW_TRACKING_AUTH as needed) or pass --tracking-uri.",
            file=sys.stderr,
        )
        return 1

    import mlflow
    import mlflow.genai

    if args.tracking_uri:
        mlflow.set_tracking_uri(args.tracking_uri)

    for prompt in prompts:
        existing = mlflow.genai.load_prompt(prompt["id"], allow_missing=True)
        unchanged = not args.force and existing is not None and existing.template == prompt["template"]

        if args.dry_run:
            if unchanged:
                print(f"[dry-run] {prompt['id']} unchanged (version {existing.version}) -- would skip")
            elif existing is None:
                print(f"[dry-run] {prompt['id']} not yet registered -- would create version 1")
            else:
                print(f"[dry-run] {prompt['id']} differs from version {existing.version} -- would create new version")
            continue

        if unchanged:
            print(f"[skip] {prompt['id']} unchanged (version {existing.version})")
            continue

        version = mlflow.genai.register_prompt(
            name=prompt["id"],
            template=prompt["template"],
            commit_message=commit_message,
            tags=_tags_for(prompt),
        )
        action = "updated" if existing is not None else "created"
        print(f"[{action}] {prompt['id']} -> version {version.version}")

    return 0
