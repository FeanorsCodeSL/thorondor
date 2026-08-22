#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from orchestrator.mcp_conformance_profile import (
    evaluate_result_directory,
    load_profile,
    validate_run_manifest,
)


def _manifest_scope(profile: dict, manifest_path: Path) -> tuple[list[str], list[str]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest must contain an object")
    return validate_run_manifest(profile, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Thorondor MCP conformance results")
    parser.add_argument("results_dir", type=Path)
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--scenario", action="append")
    args = parser.parse_args()

    profile = load_profile()
    scenario_scope = args.scenario
    result_directories = None
    if args.manifest is not None:
        try:
            if args.manifest.resolve().parent != args.results_dir.resolve():
                raise ValueError("manifest must be inside results directory")
            manifest_scope, result_directories = _manifest_scope(profile, args.manifest)
            if scenario_scope is not None and set(scenario_scope) != set(manifest_scope):
                raise ValueError("explicit scenario scope differs from manifest")
            scenario_scope = manifest_scope
        except (OSError, ValueError) as exc:
            print(
                json.dumps(
                    {
                        "claim": profile["claim"],
                        "clean": False,
                        "error": "invalid_manifest",
                        "detail": str(exc),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 1

    evaluation = evaluate_result_directory(
        profile,
        args.results_dir,
        require_all=not args.allow_missing,
        scenario_scope=scenario_scope,
        result_directories=result_directories,
    )
    print(json.dumps(evaluation.as_dict(), indent=2, sort_keys=True))
    return 0 if evaluation.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
