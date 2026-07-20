"""Offline command entry point for Phase 5 Stage A corpus generation."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import platform
import sys
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.artifacts import collect_git_provenance
from spanvouch.evaluation.corpus.generate import (
    build_corpus_plan,
    generate_phase5_corpus,
)
from spanvouch.evaluation.experiments import Phase5ExperimentConfig, load_experiment_config
from spanvouch.labs.frameworks.autogen import AutoGenRuntimeAdapter
from spanvouch.labs.frameworks.langgraph import LangGraphRuntimeAdapter
from spanvouch.labs.registry import CombinedLabEnvironmentRegistry
from spanvouch.labs.runtime import (
    AgentRuntimeAdapter,
    ExecutionProvenance,
    FrameworkId,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spanvouch labs corpus",
        allow_abbrev=False,
    )
    parser.add_argument("--mode", choices=("pilot", "formal"), default="pilot")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def build_stage_a_inputs(
    config: Phase5ExperimentConfig,
    repository_root: Path,
) -> tuple[Mapping[FrameworkId, AgentRuntimeAdapter], ExecutionProvenance]:
    """Build framework adapters and non-secret local execution provenance."""
    code = collect_git_provenance(repository_root)
    lock_sha256 = sha256((repository_root / "uv.lock").read_bytes()).hexdigest()
    scenarios: list[JsonValue] = []
    seen: set[tuple[str, str]] = set()
    for cell in build_corpus_plan(config):
        key = (cell.scenario.domain, cell.scenario.scenario_id)
        if key not in seen:
            seen.add(key)
            scenarios.append(cast(JsonValue, cell.scenario.model_dump(mode="json")))
    runtime_versions = {
        "autogen-agentchat": importlib.metadata.version("autogen-agentchat"),
        "langgraph": importlib.metadata.version("langgraph"),
        "python": platform.python_version(),
    }
    provenance = ExecutionProvenance(
        git_commit=code.git_commit,
        package_version=importlib.metadata.version("spanvouch"),
        dependency_lock_sha256=lock_sha256,
        dataset_manifest_sha256=canonical_sha256(scenarios),
        environment_sha256=canonical_sha256(
            {
                "architecture": platform.machine().lower(),
                "os": platform.system().lower(),
                "python": platform.python_version(),
            }
        ),
        tool_versions={"opslab": "1.0", "supportlab": "1.0"},
        runtime_versions=runtime_versions,
        dirty_worktree=code.dirty_worktree,
    )
    registry = CombinedLabEnvironmentRegistry()
    return (
        {
            FrameworkId.LANGGRAPH: LangGraphRuntimeAdapter(
                registry,
                provenance=provenance,
            ),
            FrameworkId.AUTOGEN: AutoGenRuntimeAdapter(
                registry,
                provenance=provenance,
            ),
        },
        provenance,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    config = load_experiment_config(arguments.config)
    if config.mode.value != arguments.mode:
        parser.error("--mode must match the checked-in experiment configuration")
    repository_root = Path.cwd()
    adapters, provenance = build_stage_a_inputs(config, repository_root)
    result = asyncio.run(
        generate_phase5_corpus(
            config=config,
            destination=arguments.output_dir,
            adapters=adapters,
            provenance=provenance,
        )
    )
    mismatches = sum(item.status == "mismatched" for item in result.parity_results)
    print(
        json.dumps(
            {
                "corpus_dir": str(arguments.output_dir),
                "entry_count": len(result.manifest.entries),
                "logical_payload_sha256": result.logical_payload_sha256,
                "manifest_sha256": result.repository.manifest_sha256,
                "parity_mismatch_count": mismatches,
            },
            sort_keys=True,
        )
    )
    return 2 if result.has_unapproved_parity_mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
