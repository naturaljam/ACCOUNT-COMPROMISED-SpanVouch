"""Build the manifest-bound Phase 5 analysis input from verified results."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import cast

from pydantic import BaseModel, JsonValue

from spanvouch.contracts.versioning import canonical_bytes, canonical_sha256
from spanvouch.evaluation.artifacts import (
    Phase5BundleConfig,
    capture_owned_directory_identity,
    create_owned_staging_directory,
    delete_owned_staging_directory,
    publish_directory_no_replace,
    quarantine_owned_staging_directory,
    read_verified_directory_tree,
)
from spanvouch.evaluation.corpus import CorpusCell
from spanvouch.evaluation.evaluate_phase5_matrix import (
    EvaluatedConditionResult,
    EvaluationPhaseManifest,
)
from spanvouch.evaluation.experiments.config import ConditionId
from spanvouch.evaluation.experiments.models import (
    ExperimentFailureCategory,
    SelectiveAction,
)
from spanvouch.evaluation.experiments.runner import (
    OutcomeStatus,
    ProviderPhaseRepository,
    ProviderPlanOutcome,
)
from spanvouch.evaluation.paper_assets import AnalysisObservation, Phase5AnalysisInput
from spanvouch.evaluation.run_phase5_analysis import EvaluatedResultsAnalysisManifest
from spanvouch.evaluation.statistics import (
    ClaimGateEvidence,
    ConditionObservation,
    holm_adjust,
    paired_cluster_bootstrap,
)
from spanvouch.evaluation.statistics.inference import ClusterBootstrapResult, MetricName
from spanvouch.evaluation.statistics.metrics import OperationalFailure, Ratio

_PRE_CANDIDATE_FAILURES = frozenset(
    {
        ExperimentFailureCategory.FRAMEWORK_EXECUTION,
        ExperimentFailureCategory.FRAMEWORK_INCOMPATIBILITY,
        ExperimentFailureCategory.INFRASTRUCTURE,
    }
)
_OPERATIONAL_FAILURES = frozenset(
    {
        ExperimentFailureCategory.FRAMEWORK_EXECUTION,
        ExperimentFailureCategory.FRAMEWORK_INCOMPATIBILITY,
        ExperimentFailureCategory.INFRASTRUCTURE,
        ExperimentFailureCategory.PROVIDER,
        ExperimentFailureCategory.CONTRACT_INVALID,
    }
)


def _model_bytes(model: BaseModel) -> bytes:
    return canonical_bytes(cast(JsonValue, model.model_dump(mode="json")))


def _cell_identity(cell: CorpusCell) -> str:
    value = cast(dict[str, object], cell.model_dump(mode="python"))
    framework = value["framework_id"]
    framework_name = getattr(framework, "value", framework)
    return ":".join(
        (
            str(value["domain"]),
            str(value["template_id"]),
            str(value["scenario_id"]),
            str(framework_name),
            str(value["repetition"]),
            str(value["seed"]),
        )
    )


def _read_json_manifest(
    root: Path,
) -> tuple[EvaluationPhaseManifest, dict[str, EvaluatedConditionResult]]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("evaluated results must be a regular directory")
    snapshot = read_verified_directory_tree(root)
    if snapshot.directories != frozenset({"results"}):
        raise ValueError("evaluated results have an unexpected directory layout")
    manifest_bytes = snapshot.files.get("manifest.json")
    if manifest_bytes is None:
        raise ValueError("evaluation manifest is missing")
    manifest = EvaluationPhaseManifest.model_validate_json(manifest_bytes)
    if _model_bytes(manifest) != manifest_bytes:
        raise ValueError("evaluation manifest is not canonical")
    expected_files = {"manifest.json", *(entry.result_path for entry in manifest.entries)}
    if set(snapshot.files) != expected_files:
        raise ValueError("evaluated results do not match their manifest")
    results: dict[str, EvaluatedConditionResult] = {}
    for entry in manifest.entries:
        content = snapshot.files[entry.result_path]
        if sha256(content).hexdigest() != entry.result_sha256:
            raise ValueError("evaluation result hash mismatch")
        result = EvaluatedConditionResult.model_validate_json(content)
        if _model_bytes(result) != content or result.plan_id != entry.plan_id:
            raise ValueError("evaluation result failed canonical verification")
        results[result.plan_id] = result
    return manifest, results


def _provider_result(
    outcome: ProviderPlanOutcome,
) -> tuple[int, int, float, Decimal]:
    result = outcome.result
    if result is None or result.usage is None:
        return 0, 0, 0.0, Decimal("0")
    return (
        result.usage.input_tokens,
        result.usage.output_tokens,
        0.0,
        result.cost_cny or Decimal("0"),
    )


def _observation(
    result: EvaluatedConditionResult,
    provider_outcome: ProviderPlanOutcome,
) -> AnalysisObservation:
    input_tokens, output_tokens, latency_ms, cost_cny = _provider_result(provider_outcome)
    category = result.failure_category
    candidate_exists = category not in _PRE_CANDIDATE_FAILURES
    family_correct = result.is_correct if candidate_exists else None
    operational_failure = cast(
        OperationalFailure,
        category.value if category in _OPERATIONAL_FAILURES else None,
    )
    action = result.selective_action
    observation = ConditionObservation(
        observation_id=result.plan_id,
        cell_id=_cell_identity(result.cell),
        cluster_id=":".join(
            (result.cell.domain, result.cell.template_id, result.cell.scenario_id)
        ),
        condition_id=result.condition_id.value,
        framework_id=result.cell.framework_id.value,
        candidate_exists=candidate_exists,
        accepted=action is SelectiveAction.ACCEPT,
        correct=result.is_correct,
        confidence=1.0 if candidate_exists else None,
        completion=result.status is OutcomeStatus.COMPLETED,
        operational_failure=operational_failure,
        family_correct=family_correct,
        causal_correct=result.is_correct if candidate_exists else None,
        grounded=result.is_correct if candidate_exists else None,
        disagreement=False,
        joint_error=(not result.is_correct if result.is_correct is not None else None),
        invalid_output=False,
        abstained=action is SelectiveAction.ABSTAIN,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_cny=cost_cny,
        latency_ms=latency_ms,
    )
    return AnalysisObservation(domain=result.cell.domain, observation=observation)


def _framework_completion_rows(
    observations: tuple[AnalysisObservation, ...],
) -> tuple[ConditionObservation, ...]:
    source = [
        item.observation
        for item in observations
        if item.observation.condition_id == ConditionId.B0.value
    ]
    pair_frameworks: dict[str, set[str]] = {}
    for row in source:
        pair_frameworks.setdefault(row.cluster_id, set()).add(row.framework_id)
    complete_pairs = {
        pair
        for pair, frameworks in pair_frameworks.items()
        if frameworks == {"langgraph", "autogen"}
    }
    rows: list[ConditionObservation] = []
    for row in source:
        if row.cluster_id not in complete_pairs:
            continue
        payload = row.model_dump(mode="python")
        if row.cell_id is None:
            raise ValueError("framework parity requires cell identities")
        parts = row.cell_id.split(":")
        pair_cell_id = ":".join((*parts[:3], *parts[4:]))
        payload.update(
            observation_id=row.observation_id,
            cell_id=pair_cell_id,
            condition_id=row.framework_id,
            cluster_id=row.cluster_id,
        )
        rows.append(ConditionObservation.model_validate(payload))
    return tuple(rows)


def _bootstrap(
    observations: tuple[AnalysisObservation, ...],
    *,
    comparison_id: str,
    reference_condition: str,
    candidate_condition: str,
    metric: MetricName,
    seed: int,
    draws: int,
    undefined_tolerance: float,
) -> ClusterBootstrapResult:
    rows = tuple(item.observation for item in observations)
    return paired_cluster_bootstrap(
        rows,
        comparison_id=comparison_id,
        reference_condition=reference_condition,
        candidate_condition=candidate_condition,
        metric=metric,
        seed=seed,
        draws=draws,
        undefined_tolerance=undefined_tolerance,
    )


def _framework_direction(
    observations: tuple[AnalysisObservation, ...],
    framework: str,
    *,
    seed: int,
    draws: int,
) -> bool:
    filtered = tuple(item for item in observations if item.observation.framework_id == framework)
    effect = _bootstrap(
        filtered,
        comparison_id=f"h2-risk-b3-minus-b2-{framework}",
        reference_condition=ConditionId.B2.value,
        candidate_condition=ConditionId.B3.value,
        metric="risk",
        seed=seed,
        draws=draws,
        undefined_tolerance=0.05,
    )
    return bool(effect.effect.estimate is not None and effect.effect.estimate < 0)


def _claim_evidence(
    observations: tuple[AnalysisObservation, ...],
    paired_effects: tuple[ClusterBootstrapResult, ...],
    *,
    bundle_config: Phase5BundleConfig,
    evaluation_manifest: EvaluationPhaseManifest,
    provider_manifest_sha256: str,
) -> ClaimGateEvidence:
    rows = tuple(item.observation for item in observations)
    counts = {
        condition.value: sum(row.condition_id == condition.value for row in rows)
        for condition in ConditionId
    }
    expected = max(counts.values(), default=0)
    complete = expected > 0 and all(value == expected for value in counts.values())
    by_framework = {
        framework: tuple(row for row in rows if row.framework_id == framework)
        for framework in ("langgraph", "autogen")
    }
    contract_valid = {
        framework: Ratio.from_counts(
            sum(row.completion for row in framework_rows), len(framework_rows)
        )
        for framework, framework_rows in by_framework.items()
    }
    effects = {effect.effect.comparison_id: effect for effect in paired_effects}
    h1 = effects["h1-completion-autogen-minus-langgraph"]
    h2_risk = effects["h2-risk-b3-minus-b2"]
    h2_coverage = effects["h2-coverage-b3-minus-b2"]
    h3_risk = effects["h3-risk-b4-minus-b2"]
    h3_coverage = effects["h3-coverage-b4-minus-b2"]
    opslab = effects["opslab-risk-b3-minus-b2"]
    h3_complete = all(
        row.completion
        for row in rows
        if row.condition_id in {ConditionId.B4.value, ConditionId.B5.value}
    )
    source_hashes = (
        provider_manifest_sha256,
        canonical_sha256(evaluation_manifest),
        evaluation_manifest.sealed_labels_manifest_sha256,
    )
    return ClaimGateEvidence(
        source_artifact_sha256s=source_hashes,
        analysis_complete=complete,
        missing_cells=sum(expected - value for value in counts.values()),
        holm=holm_adjust({"h2": 1.0, "h3": 1.0}),
        holm_alpha=0.05,
        coverage_loss_tolerance=0.05,
        langgraph_contract_valid=contract_valid["langgraph"],
        autogen_contract_valid=contract_valid["autogen"],
        h1_completion_effect=h1,
        h2_risk_effect=h2_risk,
        h2_coverage_effect=h2_coverage,
        h2_framework_beneficial={
            "langgraph": _framework_direction(
                observations,
                "langgraph",
                seed=bundle_config.analysis_seed,
                draws=bundle_config.bootstrap_draws,
            ),
            "autogen": _framework_direction(
                observations,
                "autogen",
                seed=bundle_config.analysis_seed,
                draws=bundle_config.bootstrap_draws,
            ),
        },
        h3_risk_effect=h3_risk,
        h3_coverage_effect=h3_coverage,
        h3_joint_error_effect=None,
        h3_framework_beneficial={"langgraph": False, "autogen": False},
        risk_coverage_evidence_complete=h3_complete,
        missingness_explains_gain=bool(
            h2_risk.operational_failure_explains_gain or h3_risk.operational_failure_explains_gain
        ),
        opslab_risk_effect=opslab,
        opslab_scope_limited=any(item.domain == "opslab" for item in observations),
    )


def build_phase5_analysis_input(
    *,
    evaluated_results: Path,
    provider_results: Path,
    bundle_config: Phase5BundleConfig,
) -> tuple[Phase5AnalysisInput, EvaluationPhaseManifest, str]:
    evaluation_manifest, results = _read_json_manifest(evaluated_results)
    provider_repository = ProviderPhaseRepository(provider_results)
    provider_manifest = provider_repository.verify(
        expected_manifest_sha256=bundle_config.provider_manifest_sha256
    )
    if evaluation_manifest.provider_manifest_sha256 != bundle_config.provider_manifest_sha256:
        raise ValueError("evaluation and provider manifests are not bound")
    if (
        provider_manifest.experiment_id != bundle_config.experiment_id
        or provider_manifest.corpus_manifest_sha256 != bundle_config.corpus_manifest_sha256
        or provider_manifest.candidate_manifest_sha256 != bundle_config.candidate_manifest_sha256
        or provider_manifest.matrix_manifest_sha256 != bundle_config.matrix_manifest_sha256
    ):
        raise ValueError("provider manifest does not match frozen bundle config")
    observations = tuple(
        _observation(results[entry.plan_id], provider_repository.load(entry.plan_id))
        for entry in evaluation_manifest.entries
    )
    h1_rows = tuple(
        AnalysisObservation(domain="supportlab", observation=row)
        for row in _framework_completion_rows(observations)
    )
    paired_effects = (
        _bootstrap(
            h1_rows,
            comparison_id="h1-completion-autogen-minus-langgraph",
            reference_condition="langgraph",
            candidate_condition="autogen",
            metric="completion",
            seed=bundle_config.analysis_seed,
            draws=bundle_config.bootstrap_draws,
            undefined_tolerance=0.05,
        ),
        _bootstrap(
            observations,
            comparison_id="h2-risk-b3-minus-b2",
            reference_condition=ConditionId.B2.value,
            candidate_condition=ConditionId.B3.value,
            metric="risk",
            seed=bundle_config.analysis_seed,
            draws=bundle_config.bootstrap_draws,
            undefined_tolerance=0.05,
        ),
        _bootstrap(
            observations,
            comparison_id="h2-coverage-b3-minus-b2",
            reference_condition=ConditionId.B2.value,
            candidate_condition=ConditionId.B3.value,
            metric="coverage",
            seed=bundle_config.analysis_seed,
            draws=bundle_config.bootstrap_draws,
            undefined_tolerance=0.05,
        ),
        _bootstrap(
            observations,
            comparison_id="h3-risk-b4-minus-b2",
            reference_condition=ConditionId.B2.value,
            candidate_condition=ConditionId.B4.value,
            metric="risk",
            seed=bundle_config.analysis_seed,
            draws=bundle_config.bootstrap_draws,
            undefined_tolerance=0.05,
        ),
        _bootstrap(
            observations,
            comparison_id="h3-coverage-b4-minus-b2",
            reference_condition=ConditionId.B2.value,
            candidate_condition=ConditionId.B4.value,
            metric="coverage",
            seed=bundle_config.analysis_seed,
            draws=bundle_config.bootstrap_draws,
            undefined_tolerance=0.05,
        ),
        _bootstrap(
            tuple(item for item in observations if item.domain == "opslab"),
            comparison_id="opslab-risk-b3-minus-b2",
            reference_condition=ConditionId.B2.value,
            candidate_condition=ConditionId.B3.value,
            metric="risk",
            seed=bundle_config.analysis_seed,
            draws=bundle_config.bootstrap_draws,
            undefined_tolerance=0.05,
        ),
    )
    evidence = _claim_evidence(
        observations,
        paired_effects,
        bundle_config=bundle_config,
        evaluation_manifest=evaluation_manifest,
        provider_manifest_sha256=bundle_config.provider_manifest_sha256,
    )
    analysis = Phase5AnalysisInput(
        observations=observations,
        paired_effects=paired_effects,
        claim_gate_evidence=cast(dict[str, JsonValue], evidence.model_dump(mode="json")),
    )
    analysis_manifest = EvaluatedResultsAnalysisManifest(
        schema_name="spanvouch.evaluated-results-analysis-manifest",
        schema_version="1.0",
        analysis_input_sha256=canonical_sha256(
            cast(JsonValue, analysis.model_dump(mode="json"))
        ),
    )
    if canonical_sha256(analysis_manifest) != bundle_config.evaluated_results_manifest_sha256:
        raise ValueError("analysis manifest hash does not match frozen config")
    return (
        analysis,
        evaluation_manifest,
        canonical_sha256(cast(JsonValue, analysis.model_dump(mode="json"))),
    )


def _publish_analysis_input(
    destination: Path,
    *,
    analysis: Phase5AnalysisInput,
) -> tuple[Path, Path]:
    payload = cast(JsonValue, analysis.model_dump(mode="json"))
    manifest = EvaluatedResultsAnalysisManifest(
        schema_name="spanvouch.evaluated-results-analysis-manifest",
        schema_version="1.0",
        analysis_input_sha256=canonical_sha256(payload),
    )
    staging, root_identity = create_owned_staging_directory(destination)
    identity = None
    try:
        (staging / "analysis-input.json").write_bytes(canonical_bytes(payload) + b"\n")
        (staging / "manifest.json").write_bytes(_model_bytes(manifest) + b"\n")
        identity = capture_owned_directory_identity(staging)
        publish_directory_no_replace(staging, destination)
    except Exception:
        if os.path.lexists(staging):
            if identity is None:
                quarantine_owned_staging_directory(staging, root_identity)
            else:
                delete_owned_staging_directory(staging, identity)
        raise
    return destination / "analysis-input.json", destination / "manifest.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spanvouch experiments prepare-analysis")
    parser.add_argument("--evaluated-results", type=Path, required=True)
    parser.add_argument("--provider-results", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    bundle_config = Phase5BundleConfig.model_validate_json(arguments.config.read_bytes())
    analysis, _, _ = build_phase5_analysis_input(
        evaluated_results=arguments.evaluated_results,
        provider_results=arguments.provider_results,
        bundle_config=bundle_config,
    )
    _publish_analysis_input(arguments.output_dir, analysis=analysis)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
