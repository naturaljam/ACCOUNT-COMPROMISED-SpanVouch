"""Offline command for deterministic Phase 5 statistical and paper assets."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from spanvouch.contracts.versioning import SHA256_PATTERN, canonical_sha256
from spanvouch.evaluation.artifacts import Phase5BundleConfig
from spanvouch.evaluation.paper_assets import (
    BundleConfigLike,
    ClaimGateReportLike,
    Phase5AnalysisInput,
    write_paper_assets,
)
from spanvouch.evaluation.statistics.claims import (
    ClaimGateEvidence,
    evaluate_claim_gates,
)

BundleConfigValidator = Callable[[object], BundleConfigLike]
ClaimEvaluator = Callable[[object], ClaimGateReportLike]


class EvaluatedResultsAnalysisManifest(BaseModel):
    """Minimal trusted index binding the joined offline analysis payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: Literal["spanvouch.evaluated-results-analysis-manifest"]
    schema_version: Literal["1.0"]
    analysis_input_sha256: str = Field(pattern=SHA256_PATTERN)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spanvouch experiments analyze")
    parser.add_argument("--evaluated-results", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def _validate_bundle_config(payload: object) -> BundleConfigLike:
    return Phase5BundleConfig.model_validate(payload)


def _evaluate_claim_gates(payload: object) -> ClaimGateReportLike:
    evidence = ClaimGateEvidence.model_validate(payload)
    return evaluate_claim_gates(evidence)


def _read_json(path: Path, *, label: str) -> object:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{label} must be a regular file")
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError) as error:
        raise ValueError(f"{label} is not valid JSON") from error


def run_phase5_analysis(
    *,
    evaluated_results: Path,
    config_path: Path,
    output_dir: Path,
    bundle_config_validator: BundleConfigValidator = _validate_bundle_config,
    claim_evaluator: ClaimEvaluator = _evaluate_claim_gates,
) -> tuple[Path, ...]:
    """Read joined results, apply claim gates, and publish offline assets."""
    if not evaluated_results.is_dir() or evaluated_results.is_symlink():
        raise ValueError("evaluated results must be a regular directory")
    analysis_path = evaluated_results / "analysis-input.json"
    analysis_payload = _read_json(analysis_path, label="analysis input")
    results_manifest = EvaluatedResultsAnalysisManifest.model_validate(
        _read_json(evaluated_results / "manifest.json", label="evaluated-results manifest")
    )
    bundle_config = bundle_config_validator(
        _read_json(config_path, label="Phase 5 bundle config")
    )
    if (
        canonical_sha256(cast(JsonValue, results_manifest.model_dump(mode="json")))
        != bundle_config.evaluated_results_manifest_sha256
    ):
        raise ValueError("evaluated-results manifest hash does not match frozen config")
    if (
        canonical_sha256(cast(JsonValue, analysis_payload))
        != results_manifest.analysis_input_sha256
    ):
        raise ValueError("analysis input SHA-256 does not match evaluated-results manifest")
    analysis = Phase5AnalysisInput.model_validate(analysis_payload)
    claim_payload = cast(object, analysis.claim_gate_evidence)
    claim_report = claim_evaluator(claim_payload)
    return write_paper_assets(
        output_dir,
        analysis=analysis,
        bundle_config=bundle_config,
        claim_gate_report=claim_report,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    bundle_config_validator: BundleConfigValidator = _validate_bundle_config,
    claim_evaluator: ClaimEvaluator = _evaluate_claim_gates,
) -> int:
    arguments = _parser().parse_args(argv)
    run_phase5_analysis(
        evaluated_results=arguments.evaluated_results,
        config_path=arguments.config,
        output_dir=arguments.output_dir,
        bundle_config_validator=bundle_config_validator,
        claim_evaluator=claim_evaluator,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
