"""CLI entrypoint for loading every service's prompts.json into the MLflow
Prompt Registry.

Each service that owns a prompt keeps its own prompts.json (sibling of its
src/, e.g. local-dc-agent/prompts.json, local-wms-api/prompts.json) -- baked
into that service's own container image, so PROMPT_SOURCE=local works in a
deployed pod without MLflow, and so each Containerfile only ever COPYs files
from its own build context. This tool discovers every such file across the
repo and registers them all in one pass, rather than reading one central
catalog.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# prompt-registry/src/cli.py -> prompt-registry/src -> prompt-registry -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

logger = logging.getLogger(__name__)


def discover_catalog_files(repo_root: Path) -> list[Path]:
    """Every <service>/prompts.json in the repo, one level deep, sorted for
    stable output. Doesn't recurse into .git, target/, or any .venv -- glob's
    single-level `*/prompts.json` pattern already excludes those (they don't
    have a prompts.json directly inside them)."""
    return sorted(repo_root.glob("*/prompts.json"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="src",
        description=(
            "Register every prompt from every <service>/prompts.json in the repo "
            "as a version in the MLflow Prompt Registry."
        ),
    )
    parser.add_argument(
        "--file",
        "-f",
        action="append",
        default=None,
        help="Register from this catalog file instead of auto-discovering every "
        "<service>/prompts.json (repeatable). Default: discover automatically.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        help="Prompt id to load (repeatable). Default: load every prompt found.",
    )
    parser.add_argument(
        "--commit-message",
        "-m",
        default=None,
        help="Commit message applied to every version registered in this run. "
        "Default: a per-prompt message noting its source catalog file.",
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
    parser.add_argument(
        "--prune-old-versions",
        action="store_true",
        help="After registering, delete every version of each processed prompt except the "
        "current latest, keeping only the newest version's history. Scoped to whichever "
        "prompts this run processed (respects --only/--file). Requires a live MLflow "
        "connection even with --dry-run, since it needs to see what's currently registered.",
    )
    parser.add_argument(
        "--prune-unused",
        action="store_true",
        help="Delete prompts (all versions, then the prompt itself) that exist in the "
        "registry's namespace/workspace but no longer appear in any <service>/prompts.json "
        "in the repo. Always compares against every catalog file regardless of --only/--file, "
        "since 'unused' is a whole-namespace property. Requires a live MLflow connection "
        "even with --dry-run.",
    )
    return parser.parse_args(argv)


def load_catalog(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    prompts = data["prompts"]
    for prompt in prompts:
        prompt["_source_catalog"] = path
    return prompts


def load_catalogs(paths: list[Path]) -> list[dict[str, Any]]:
    prompts: list[dict[str, Any]] = []
    seen: dict[str, Path] = {}
    for path in paths:
        for prompt in load_catalog(path):
            prior = seen.get(prompt["id"])
            if prior is not None:
                raise ValueError(
                    f"duplicate prompt id {prompt['id']!r} found in both {prior} and {path}"
                )
            seen[prompt["id"]] = path
            prompts.append(prompt)
    if not prompts:
        raise ValueError(f"No prompts found across {len(paths)} catalog file(s): {paths}")
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


def _commit_message_for(prompt: dict[str, Any], override: str | None) -> str:
    if override:
        return override
    source = prompt["_source_catalog"]
    try:
        source = source.relative_to(REPO_ROOT)
    except ValueError:
        pass
    return f"Loaded from {source}"


def prune_old_versions(client: Any, prompts: list[dict[str, Any]], dry_run: bool) -> None:
    """Delete every version of each given prompt except the current latest.

    Verifies the kept version is still readable after each deletion: this MLflow
    deployment has been observed to drop an entire prompt (every version, including
    the one meant to survive) as a side effect of deleting just its oldest version,
    so silently trusting the delete call isn't safe here.
    """
    for prompt in prompts:
        name = prompt["id"]
        versions = sorted(client.search_prompt_versions(name), key=lambda v: int(v.version))
        if len(versions) <= 1:
            continue
        latest = versions[-1]
        for version in versions[:-1]:
            if dry_run:
                print(f"[dry-run] would prune {name} v{version.version} (superseded by v{latest.version})")
                continue
            client.delete_prompt_version(name, version.version)
            print(f"[pruned] {name} v{version.version} (kept v{latest.version})")

        if dry_run:
            continue
        try:
            client.get_prompt_version(name, latest.version)
        except Exception:
            print(
                f"[ERROR] {name} v{latest.version} is no longer retrievable after pruning -- "
                f"the registry appears to have dropped the whole prompt. Restore it with: "
                f"python -m src --only {name}",
                file=sys.stderr,
            )


def prune_unused_prompts(client: Any, known_ids: set[str], dry_run: bool) -> None:
    """Delete prompts registered in MLflow that no longer appear in any catalog file."""
    unused = sorted(p.name for p in client.search_prompts() if p.name not in known_ids)
    if not unused:
        print("[prune-unused] nothing to prune -- every registered prompt is still cataloged")
        return
    for name in unused:
        if dry_run:
            print(f"[dry-run] would delete prompt '{name}' (no longer cataloged) and all its versions")
            continue
        for version in client.search_prompt_versions(name):
            client.delete_prompt_version(name, version.version)
        try:
            client.delete_prompt(name)
        except Exception as exc:
            # Observed on this deployment: deleting a prompt's last remaining version can
            # already remove the prompt itself, making this call redundant -- fine, that's
            # the end state we wanted anyway.
            if "RESOURCE_DOES_NOT_EXIST" not in str(exc):
                raise
        print(f"[pruned] {name} (prompt + all versions removed)")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"))
    args = parse_args(argv)

    catalog_files = [Path(f) for f in args.file] if args.file else discover_catalog_files(REPO_ROOT)
    if not catalog_files:
        print(f"error: no prompts.json files found under {REPO_ROOT}", file=sys.stderr)
        return 1

    try:
        prompts = load_catalogs(catalog_files)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.only:
        wanted = set(args.only)
        prompts = [p for p in prompts if p["id"] in wanted]
        missing = wanted - {p["id"] for p in prompts}
        if missing:
            print(f"error: unknown prompt id(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 1

    needs_live_connection = args.prune_old_versions or args.prune_unused
    tracking_uri = args.tracking_uri or os.environ.get("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        if needs_live_connection:
            print(
                "error: --prune-old-versions/--prune-unused need a live MLflow connection "
                "(to see what's currently registered) even with --dry-run. Set "
                "MLFLOW_TRACKING_URI or pass --tracking-uri.",
                file=sys.stderr,
            )
            return 1
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
    from mlflow import MlflowClient

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
            commit_message=_commit_message_for(prompt, args.commit_message),
            tags=_tags_for(prompt),
        )
        action = "updated" if existing is not None else "created"
        print(f"[{action}] {prompt['id']} -> version {version.version}")

    if args.prune_old_versions or args.prune_unused:
        client = MlflowClient()

        if args.prune_old_versions:
            prune_old_versions(client, prompts, args.dry_run)

        if args.prune_unused:
            known_ids = {p["id"] for p in load_catalogs(discover_catalog_files(REPO_ROOT))}
            prune_unused_prompts(client, known_ids, args.dry_run)

    return 0
