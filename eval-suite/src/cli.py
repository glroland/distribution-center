"""CLI entrypoint. Runs the three benchmarks directly against whatever
services are configured (settings.py / env vars) and prints a pass/fail
report -- no EvalHub installation required. This is the same benchmark logic
each adapters/*_adapter.py module also exposes as a FrameworkAdapter for
`evalhub eval run` once EvalHub is deployed; see the module docstrings.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from .adapters import end_to_end_adapter, extraction_adapter, mcp_trajectory_adapter
from .adapters.base import LocalRunResult

_ADAPTERS = {
    "extraction": extraction_adapter,
    "mcp-trajectory": mcp_trajectory_adapter,
    "end-to-end": end_to_end_adapter,
}

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="src",
        description=(
            "Run the distribution-center EvalHub benchmarks locally, against the "
            "services configured in settings.py/.env. Requires po-ingest-api and "
            "local-dc-agent (plus its five downstream MCP servers) to be running "
            "-- e.g. `make start-all` from the repo root."
        ),
    )
    parser.add_argument(
        "--adapter",
        "-a",
        choices=[*_ADAPTERS, "all"],
        default="all",
        help="Which benchmark to run. Default: all",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=5,
        help="Number of golden PO cases for the extraction benchmark. Default: 5",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed for the extraction benchmark's generated dataset. Default: 42",
    )
    return parser.parse_args(argv)


def _print_result(name: str, result: LocalRunResult) -> None:
    status = "PASS" if result.passed else "FAIL"
    print(f"\n=== {name} ({result.benchmark_id}): {status} ===")
    print(f"  score={result.score:.3f}  threshold={result.threshold:.3f}  n={result.num_examples}")
    for key, value in result.metrics.items():
        print(f"  {key}={value}")
    if result.details:
        print("  details:")
        for line in result.details[:20]:
            print(f"    - {line}")
        if len(result.details) > 20:
            print(f"    ... and {len(result.details) - 20} more")


async def _run_all(names: list[str], n: int, seed: int) -> dict[str, LocalRunResult]:
    results: dict[str, LocalRunResult] = {}
    for name in names:
        module = _ADAPTERS[name]
        logger.info("Running benchmark: %s", name)
        if name == "extraction":
            results[name] = await module.run_local(n=n, seed=seed)
        else:
            results[name] = await module.run_local()
    return results


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "WARNING"),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    args = parse_args(argv)
    names = list(_ADAPTERS) if args.adapter == "all" else [args.adapter]

    try:
        results = asyncio.run(_run_all(names, args.n, args.seed))
    except Exception as exc:  # noqa: BLE001 - surface as a clean CLI failure, not a traceback dump
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    for name, result in results.items():
        _print_result(name, result)

    passed_count = sum(1 for r in results.values() if r.passed)
    all_passed = passed_count == len(results)
    print(f"\n{'ALL PASSED' if all_passed else 'FAILED'} ({passed_count}/{len(results)} benchmarks passed)")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
