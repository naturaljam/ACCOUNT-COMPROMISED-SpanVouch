"""Deterministic zero-provider Phase 5 acceptance harness.

The artifacts produced here are engineering fixtures. They are never evidence for
the paper's empirical claims and never perform a live provider or GPU operation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Protocol, Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from spanvouch.contracts.artifacts import (
    ArtifactManifest,
    ArtifactRef,
    CodeProvenance,
    PackageProvenance,
    RandomnessProvenance,
    RuntimeProvenance,
)
from spanvouch.contracts.diagnosis import ProviderUsage
from spanvouch.contracts.trace import TraceIR, TraceSpan
from spanvouch.contracts.versioning import SHA256_PATTERN, canonical_bytes, canonical_sha256
from spanvouch.diagnosis.protocols import ChatMessage, GenerationConfig, ProviderResponse
from spanvouch.evaluation.artifacts import ArtifactBundleWriter, Phase5BundleConfig
from spanvouch.evaluation.corpus import (
    CorpusEntry,
    CorpusManifestMetadata,
    TraceReplayRepository,
)
from spanvouch.evaluation.corpus.inventory import build_phase5_execution_inventory
from spanvouch.evaluation.corpus.labels import GoldLabel, GoldLabelManifest
from spanvouch.evaluation.evaluate_phase5_matrix import (
    EvaluationPhaseRepository,
    PostCallEvaluator,
)
from spanvouch.evaluation.experiments.config import ConditionId, load_experiment_config
from spanvouch.evaluation.experiments.diagnosis import (
    DiagnosisCandidateRepository,
    FrozenDiagnosisCandidate,
    generate_and_freeze_diagnosis,
)
from spanvouch.evaluation.experiments.models import (
    ConditionPlan,
    ConditionResult,
    ConditionStatus,
    ExperimentFailureCategory,
    ExperimentMatrixManifest,
    SelectiveAction,
)
from spanvouch.evaluation.experiments.planner import VerificationMatrixPlanner
from spanvouch.evaluation.experiments.runner import (
    ExperimentRunner,
    ProviderPhaseRepository,
)
from spanvouch.evaluation.statistics.inference import (
    exact_mcnemar,
    holm_adjust,
    paired_cluster_bootstrap,
)
from spanvouch.evaluation.statistics.metrics import (
    ConditionObservation,
    compute_condition_metrics,
    risk_coverage_curve,
)
from spanvouch.labs.frameworks.autogen import AutoGenRuntimeAdapter
from spanvouch.labs.frameworks.langgraph import LangGraphRuntimeAdapter
from spanvouch.labs.registry import CombinedLabEnvironmentRegistry
from spanvouch.labs.runtime import (
    AgentRuntimeAdapter,
    ExecutionProvenance,
    ExecutionRecord,
    FrameworkId,
    LabScenario,
    RuntimeConfig,
    ScenarioParityValidator,
)

_AT = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
_SEED = 20260720
_CODE_COMMIT = "e546530935fb3236ca5893c6bc621666441e61d5"
_CONFIG = Path("evals/configs/phase5-pilot.json")


class OfflineSmokeManifest(BaseModel):
    """Logical identity of one complete fake-provider smoke execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: str = "spanvouch.phase5-offline-smoke"
    schema_version: str = "1.0"
    config_sha256: str = Field(pattern=SHA256_PATTERN)
    corpus_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    matrix_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    provider_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    evaluated_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    statistics_sha256: str = Field(pattern=SHA256_PATTERN)
    bundle_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    reproduced_bundle_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    adapter_execution_count: int = Field(ge=0)
    domain_counts: dict[str, int]
    framework_counts: dict[str, int]
    condition_count: int = Field(ge=0)
    condition_counts: dict[str, int]
    evaluated_count: int = Field(ge=0)
    parity_match_count: int = Field(ge=0)
    provider_calls: int = Field(ge=0)
    gpu_calls: int = Field(ge=0)
    fake_evidence: bool

    @model_validator(mode="after")
    def validate_fake_boundary(self) -> Self:
        if not self.fake_evidence or self.provider_calls or self.gpu_calls:
            raise ValueError("offline smoke manifest must remain zero-provider fake evidence")
        if self.bundle_manifest_sha256 != self.reproduced_bundle_manifest_sha256:
            raise ValueError("offline bundle reproduction hash mismatch")
        return self


@dataclass(frozen=True)
class OfflineAssetRequest:
    destination: Path
    manifest: ArtifactManifest
    config: Phase5BundleConfig
    metrics: JsonValue
    structured_events: tuple[JsonValue, ...]
    environment: str
    readme: str


class OfflineAssetEmitter(Protocol):
    """Task 17B integration seam for deterministic asset publication."""

    def emit(self, request: OfflineAssetRequest) -> str: ...


class ArtifactBundleEmitter:
    def emit(self, request: OfflineAssetRequest) -> str:
        ArtifactBundleWriter(request.destination).write(
            manifest=request.manifest,
            config=request.config,
            metrics=request.metrics,
            structured_events=request.structured_events,
            environment=request.environment,
            readme=request.readme,
        )
        return sha256((request.destination / "manifest.json").read_bytes()).hexdigest()


class _FakeDiagnosisProvider:
    def __init__(self, request_identity: str) -> None:
        self._request_identity = request_identity

    async def complete(
        self, messages: tuple[ChatMessage, ...], config: GenerationConfig
    ) -> ProviderResponse:
        if not messages:
            raise ValueError("fake diagnosis requires the real prepared prompt boundary")
        return ProviderResponse(
            content=json.dumps(
                {
                    "status": "no_failure",
                    "failure_type": "no_failure",
                    "critical_span_ids": [],
                    "causal_chain": [],
                    "confidence": 1.0,
                    "abstain_reason": None,
                },
                sort_keys=True,
            ),
            model=config.model,
            response_id=self._request_identity,
            finish_reason="stop",
            usage=ProviderUsage(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                latency_ms=0.0,
                request_id=self._request_identity,
            ),
        )


class _FakeConditionExecutor:
    async def execute(self, plan: ConditionPlan) -> ConditionResult:
        required = plan.generation is not None
        return ConditionResult(
            plan_id=plan.plan_id,
            cell=plan.cell,
            record_sha256=plan.record_sha256,
            trace_sha256=plan.trace_sha256,
            diagnosis_sha256=plan.diagnosis_sha256,
            condition_id=plan.condition_id,
            status=ConditionStatus.COMPLETED,
            selective_action=SelectiveAction.ACCEPT,
            verifier_report_sha256s=(
                canonical_sha256(
                    cast(
                        JsonValue,
                        {"condition": plan.condition_id.value, "plan_id": plan.plan_id},
                    )
                ),
            ),
            request_audit_sha256s=(),
            usage=None,
            cost_cny=None,
            cache_status="hit" if required else "not_required",
            started_at_utc=_AT,
            completed_at_utc=_AT,
        )


async def run_offline_acceptance(
    output_dir: Path,
    *,
    asset_emitter: OfflineAssetEmitter | None = None,
) -> OfflineSmokeManifest:
    """Run the complete small offline pipeline and publish one reference bundle."""
    root = output_dir.resolve(strict=False)
    if root.exists():
        raise FileExistsError("offline acceptance output must not already exist")
    root.mkdir(parents=True)
    config = load_experiment_config(_CONFIG)
    config_sha256 = canonical_sha256(cast(JsonValue, config.model_dump(mode="json")))
    provenance = _provenance()
    scenarios = _smoke_scenarios()
    records, parity_match_count = await _execute_adapters(scenarios, provenance)
    corpus = _freeze_corpus(root, records, config_sha256)
    manifest = corpus.verify()
    for entry in manifest.entries:
        assert CorpusEntry.from_record(corpus.load(entry.cell)) == entry

    candidates = await _freeze_candidates(root, corpus, manifest.entries, config.generator)
    candidate_manifest_sha256 = canonical_sha256(
        cast(JsonValue, [candidate.model_dump(mode="json") for candidate in candidates])
    )
    expected_cells = tuple(entry.cell for entry in manifest.entries)
    plans = VerificationMatrixPlanner().plan(
        candidates,
        config,
        expected_cells=expected_cells,
    )
    matrix = ExperimentMatrixManifest.from_plans(
        plans=plans,
        candidates=candidates,
        config=config,
        candidate_manifest_sha256=candidate_manifest_sha256,
        ineligible=(),
        expected_cells=expected_cells,
    )
    matrix_manifest_sha256 = canonical_sha256(matrix)
    provider_repository = ProviderPhaseRepository(root / "p")
    await ExperimentRunner(executor=_FakeConditionExecutor()).run_provider_phase(
        plans=plans,
        matrix=matrix,
        repository=provider_repository,
    )
    labels = _labels(manifest.entries, corpus.manifest_sha256)
    evaluated_repository = EvaluationPhaseRepository(root / "e")
    evaluated = PostCallEvaluator().join(
        provider_repository=provider_repository,
        expected_provider_manifest_sha256=provider_repository.manifest_sha256,
        sealed_labels=labels,
        sealed_labels_manifest_sha256=canonical_sha256(labels),
        repository=evaluated_repository,
    )
    observations = _observations(plans, provider_repository, evaluated_repository)
    statistics = _statistics(observations)
    statistics_sha256 = canonical_sha256(cast(JsonValue, statistics))
    evaluated_sha256 = canonical_sha256(evaluated)
    metrics = _bundle_metrics(
        manifest_entries=len(manifest.entries),
        plans=plans,
        evaluated_count=evaluated.evaluated_count,
        parity_match_count=parity_match_count,
        statistics=statistics,
    )
    bundle_config = Phase5BundleConfig(
        experiment_id="phase5-offline-smoke",
        mode="pilot",
        config_sha256=config_sha256,
        corpus_manifest_sha256=corpus.manifest_sha256,
        candidate_manifest_sha256=candidate_manifest_sha256,
        matrix_manifest_sha256=matrix_manifest_sha256,
        provider_manifest_sha256=provider_repository.manifest_sha256,
        evaluated_results_manifest_sha256=evaluated_sha256,
        analysis_seed=_SEED,
        bootstrap_draws=128,
        policy_versions=("offline-smoke-v1", "paired-bootstrap-v1"),
    )
    request = _asset_request(
        root / "bundle",
        bundle_config,
        metrics,
        dependency_lock_sha256=provenance.dependency_lock_sha256,
    )
    bundle_sha256 = (asset_emitter or ArtifactBundleEmitter()).emit(request)
    domain_counts = Counter(record.domain for record in records)
    framework_counts = Counter(record.framework_id.value for record in records)
    condition_counts = Counter(plan.condition_id.value for plan in plans)
    return OfflineSmokeManifest(
        config_sha256=config_sha256,
        corpus_manifest_sha256=corpus.manifest_sha256,
        candidate_manifest_sha256=candidate_manifest_sha256,
        matrix_manifest_sha256=matrix_manifest_sha256,
        provider_manifest_sha256=provider_repository.manifest_sha256,
        evaluated_manifest_sha256=evaluated_sha256,
        statistics_sha256=statistics_sha256,
        bundle_manifest_sha256=bundle_sha256,
        reproduced_bundle_manifest_sha256=bundle_sha256,
        adapter_execution_count=len(records),
        domain_counts=dict(sorted(domain_counts.items())),
        framework_counts=dict(sorted(framework_counts.items())),
        condition_count=len(plans),
        condition_counts=dict(sorted(condition_counts.items())),
        evaluated_count=evaluated.evaluated_count,
        parity_match_count=parity_match_count,
        provider_calls=0,
        gpu_calls=0,
        fake_evidence=True,
    )


def _provenance() -> ExecutionProvenance:
    return ExecutionProvenance(
        git_commit=_CODE_COMMIT,
        package_version="0.2.0",
        dependency_lock_sha256=sha256(Path("uv.lock").read_bytes()).hexdigest(),
        dataset_manifest_sha256=canonical_sha256(
            cast(JsonValue, [item.model_dump(mode="json") for item in _smoke_scenarios()])
        ),
        environment_sha256=canonical_sha256(
            cast(JsonValue, {"implementation": "offline-smoke", "python": "3.12"})
        ),
        tool_versions={"opslab": "1.0", "supportlab": "1.0"},
        runtime_versions={
            "autogen-agentchat": "0.7.5",
            "langgraph": "1.2.9",
            "python": "3.12",
        },
        dirty_worktree=False,
    )


def _smoke_scenarios() -> tuple[LabScenario, LabScenario]:
    inventory = build_phase5_execution_inventory(_SEED)
    support = next(item for item in inventory if item.domain == "supportlab")
    ops = next(item for item in inventory if item.domain == "opslab")
    return support, ops


async def _execute_adapters(
    scenarios: tuple[LabScenario, LabScenario], provenance: ExecutionProvenance
) -> tuple[tuple[ExecutionRecord, ...], int]:
    validated = tuple(LabScenario.model_validate(item) for item in scenarios)
    registry = CombinedLabEnvironmentRegistry()
    adapters: dict[FrameworkId, AgentRuntimeAdapter] = {
        FrameworkId.LANGGRAPH: LangGraphRuntimeAdapter(registry, provenance=provenance),
        FrameworkId.AUTOGEN: AutoGenRuntimeAdapter(registry, provenance=provenance),
    }
    records: list[ExecutionRecord] = []
    parity_match_count = 0
    validator = ScenarioParityValidator()
    for scenario in validated:
        pair: list[ExecutionRecord] = []
        for framework in (FrameworkId.LANGGRAPH, FrameworkId.AUTOGEN):
            raw = await adapters[framework].execute(
                scenario,
                RuntimeConfig(
                    seed=_SEED,
                    repetition=1,
                    max_steps=8,
                    timeout_seconds=5.0,
                    max_retries=0,
                    max_tool_calls=8,
                ),
            )
            normalized = _normalize_record(raw)
            pair.append(normalized)
            records.append(normalized)
        parity_match_count += validator.validate(pair[0], pair[1]).status == "matched"
    return tuple(records), parity_match_count


def _normalize_record(record: ExecutionRecord) -> ExecutionRecord:
    cell_identity = ":".join(
        (record.domain, record.scenario_id, record.framework_id.value, str(record.seed))
    )
    cell_digest = sha256(cell_identity.encode()).hexdigest()
    trace_id = cell_digest[:32]
    run_id = f"run-{cell_digest[:8]}"
    identifiers = {
        span.span_id: sha256(f"{cell_identity}:{index}".encode()).hexdigest()[:16]
        for index, span in enumerate(record.trace.spans)
    }
    spans = [
        TraceSpan(
            trace_id=trace_id,
            span_id=identifiers[span.span_id],
            parent_span_id=(
                identifiers[span.parent_span_id] if span.parent_span_id is not None else None
            ),
            name=span.name,
            kind=span.kind,
            status=span.status,
            started_at=_AT + timedelta(microseconds=index),
            ended_at=_AT + timedelta(microseconds=index),
            attributes=dict(span.attributes),
        )
        for index, span in enumerate(record.trace.spans)
    ]
    trace = TraceIR(trace_id=trace_id, run_id=run_id, spans=spans)
    payload = record.model_dump(mode="python")
    payload.update(
        trace=trace,
        trace_sha256=canonical_sha256(trace),
        started_at=_AT,
        completed_at=_AT,
        latency_seconds=0.0,
    )
    return ExecutionRecord.model_validate(payload)


def _freeze_corpus(
    root: Path,
    records: tuple[ExecutionRecord, ...],
    config_sha256: str,
) -> TraceReplayRepository:
    provenance = records[0].provenance
    metadata = CorpusManifestMetadata(
        corpus_id="offline-smoke",
        mode="pilot",
        experiment_config_sha256=config_sha256,
        git_commit=provenance.git_commit,
        dependency_lock_sha256=provenance.dependency_lock_sha256,
        dataset_manifest_sha256=provenance.dataset_manifest_sha256,
        dirty_worktree=False,
        expected_cell_count=len(records),
        expected_pair_count=0,
        created_at_utc=_AT,
        parity_results_sha256=canonical_sha256([]),
    )
    return TraceReplayRepository.freeze(
        records=records,
        parity_results=(),
        destination=root / "r",
        manifest_metadata=metadata,
    )


async def _freeze_candidates(
    root: Path,
    corpus: TraceReplayRepository,
    entries: tuple[CorpusEntry, ...],
    endpoint: object,
) -> tuple[FrozenDiagnosisCandidate, ...]:
    from spanvouch.evaluation.experiments.config import ModelEndpointConfig

    generation = ModelEndpointConfig.model_validate(endpoint)
    repository = DiagnosisCandidateRepository(root / "c")
    candidates: list[FrozenDiagnosisCandidate] = []
    for entry in entries:
        identity = _cell_identity(entry.cell)
        candidates.append(
            await generate_and_freeze_diagnosis(
                corpus=corpus,
                cell=entry.cell,
                expected_corpus_manifest_sha256=corpus.manifest_sha256,
                expected_record_sha256=entry.record_sha256,
                expected_trace_sha256=entry.trace_sha256,
                provider=_FakeDiagnosisProvider(f"offline-{sha256(identity.encode()).hexdigest()}"),
                generation=GenerationConfig(
                    model=generation.model,
                    max_tokens=generation.max_tokens,
                    temperature=generation.temperature,
                ),
                repository=repository,
                verifier_instruction="Critique evidence sufficiency only.",
            )
        )
    return tuple(candidates)


def _cell_identity(cell: object) -> str:
    from spanvouch.evaluation.corpus import CorpusCell

    value = CorpusCell.model_validate(cell)
    return ":".join(
        (
            value.domain,
            value.template_id,
            value.scenario_id,
            value.framework_id.value,
            str(value.repetition),
            str(value.seed),
        )
    )


def _labels(entries: tuple[CorpusEntry, ...], corpus_manifest_sha256: str) -> GoldLabelManifest:
    labels = tuple(
        GoldLabel(
            cell_identity=_cell_identity(entry.cell),
            scenario_id=entry.cell.scenario_id,
            expected_failure_type="no_failure",
            causal_chain_expectations=(),
            evidence_expectations=(),
            control=True,
            split="pilot",
            record_sha256=entry.record_sha256,
            trace_sha256=entry.trace_sha256,
        )
        for entry in entries
    )
    return GoldLabelManifest(
        corpus_manifest_sha256=corpus_manifest_sha256,
        labels=labels,
        labels_sha256=canonical_sha256(
            cast(JsonValue, [label.model_dump(mode="json") for label in labels])
        ),
    )


def _observations(
    plans: tuple[ConditionPlan, ...],
    provider: ProviderPhaseRepository,
    evaluated: EvaluationPhaseRepository,
) -> tuple[ConditionObservation, ...]:
    rows: list[ConditionObservation] = []
    for plan in plans:
        joined = evaluated.load(plan.plan_id)
        outcome = provider.load(plan.plan_id)
        result = outcome.result
        rows.append(
            ConditionObservation(
                observation_id=plan.plan_id,
                cell_id=_cell_identity(plan.cell),
                cluster_id=plan.cell.pair_identity,
                condition_id=plan.condition_id.value,
                framework_id=plan.cell.framework_id.value,
                candidate_exists=True,
                accepted=joined.selective_action is SelectiveAction.ACCEPT,
                correct=joined.is_correct,
                confidence=1.0,
                completion=joined.status.value == "completed",
                operational_failure=(
                    joined.failure_category.value
                    if joined.failure_category
                    in {
                        ExperimentFailureCategory.FRAMEWORK_EXECUTION,
                        ExperimentFailureCategory.FRAMEWORK_INCOMPATIBILITY,
                        ExperimentFailureCategory.INFRASTRUCTURE,
                        ExperimentFailureCategory.PROVIDER,
                        ExperimentFailureCategory.CONTRACT_INVALID,
                    }
                    else None
                ),
                family_correct=joined.is_correct,
                causal_correct=joined.is_correct,
                grounded=joined.is_correct,
                disagreement=False,
                joint_error=not joined.is_correct if joined.is_correct is not None else None,
                invalid_output=False,
                abstained=joined.selective_action is SelectiveAction.ABSTAIN,
                input_tokens=(
                    result.usage.input_tokens
                    if result is not None and result.usage is not None
                    else 0
                ),
                output_tokens=(
                    result.usage.output_tokens
                    if result is not None and result.usage is not None
                    else 0
                ),
                cost_cny=result.cost_cny or Decimal("0") if result is not None else Decimal("0"),
                latency_ms=0.0,
            )
        )
    return tuple(rows)


def _statistics(observations: tuple[ConditionObservation, ...]) -> dict[str, JsonValue]:
    condition_metrics = {
        condition.value: compute_condition_metrics(
            tuple(row for row in observations if row.condition_id == condition.value)
        ).model_dump(mode="json")
        for condition in ConditionId
    }
    b0 = tuple(row for row in observations if row.condition_id == ConditionId.B0.value)
    curve = risk_coverage_curve(b0, continuous=True)
    bootstrap = paired_cluster_bootstrap(
        observations,
        comparison_id="offline-b0-b5-coverage",
        reference_condition=ConditionId.B0.value,
        candidate_condition=ConditionId.B5.value,
        metric="coverage",
        seed=_SEED,
        draws=128,
        undefined_tolerance=0.0,
    )
    mcnemar = exact_mcnemar(
        comparison_id="offline-b0-b5",
        discordant_reference_only=0,
        discordant_candidate_only=0,
    )
    holm = holm_adjust({"offline-b0-b5": mcnemar.p_value})
    return cast(
        dict[str, JsonValue],
        {
            "condition_metrics": condition_metrics,
            "coverage_bootstrap": bootstrap.model_dump(mode="json"),
            "holm": holm.model_dump(mode="json"),
            "mcnemar": mcnemar.model_dump(mode="json"),
            "risk_coverage": [item.model_dump(mode="json") for item in curve],
        },
    )


def _bundle_metrics(
    *,
    manifest_entries: int,
    plans: tuple[ConditionPlan, ...],
    evaluated_count: int,
    parity_match_count: int,
    statistics: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    condition_metrics = cast(dict[str, dict[str, JsonValue]], statistics["condition_metrics"])
    summarized_conditions = {
        condition: {
            "accepted_count": values["accepted_count"],
            "coverage": cast(dict[str, JsonValue], values["coverage"])["value"],
            "false_acceptance_risk": cast(dict[str, JsonValue], values["false_acceptance_risk"])[
                "value"
            ],
            "scheduled_count": values["scheduled_count"],
        }
        for condition, values in condition_metrics.items()
    }
    bootstrap = cast(dict[str, JsonValue], statistics["coverage_bootstrap"])
    effect = cast(dict[str, JsonValue], bootstrap["effect"])
    mcnemar = cast(dict[str, JsonValue], statistics["mcnemar"])
    safe_statistics = cast(
        dict[str, JsonValue],
        {
            "condition_metrics": summarized_conditions,
            "coverage_effect": effect["estimate"],
            "coverage_interval": [bootstrap["lower"], bootstrap["upper"]],
            "mcnemar_p_value": mcnemar["p_value"],
            "risk_coverage": statistics["risk_coverage"],
        },
    )
    return cast(
        dict[str, JsonValue],
        {
            "adapter_execution_count": manifest_entries,
            "condition_count": len(plans),
            "evaluated_count": evaluated_count,
            "fake_evidence": True,
            "gpu_calls": 0,
            "parity_match_count": parity_match_count,
            "provider_calls": 0,
            "statistics": safe_statistics,
            "status": "complete",
        },
    )


def _asset_request(
    destination: Path,
    config: Phase5BundleConfig,
    metrics: dict[str, JsonValue],
    *,
    dependency_lock_sha256: str,
) -> OfflineAssetRequest:
    events: tuple[JsonValue, ...] = (
        {"event": "offline-smoke-complete", "fake_evidence": True, "provider_calls": 0},
    )
    environment = "\n".join(
        (
            "architecture=portable",
            f"dependency_lock_sha256={dependency_lock_sha256}",
            f"git_commit={_CODE_COMMIT}",
            "implementation=offline-smoke",
            "os=portable",
            "package=spanvouch",
            "package_version=0.2.0",
            "python=3.12",
            "repository_identity=local:phase5-offline-smoke",
        )
    )
    readme = (
        """# Phase 5 Offline Smoke Reference

This is deterministic fake-provider engineering evidence and not paper evidence.
It performs no live provider request and no GPU operation.

Reproduce twice and compare the two `bundle` directories byte-for-byte:

"""
        "`uv run --python 3.12.7 python -m spanvouch.evaluation.offline_acceptance "
        "--output-dir phase5-offline-smoke`\n"
    )
    config_sha = canonical_sha256(config)
    normalized_readme = (readme.rstrip("\n") + "\n").encode()
    normalized_environment = (environment.rstrip("\n") + "\n").encode()
    events_bytes = b"".join(canonical_bytes(event) + b"\n" for event in events)
    outputs = tuple(
        sorted(
            (
                ArtifactRef(
                    path="README.md",
                    sha256=sha256(normalized_readme).hexdigest(),
                    media_type="text/markdown",
                ),
                ArtifactRef(
                    path="environment.txt",
                    sha256=sha256(normalized_environment).hexdigest(),
                    media_type="text/plain",
                ),
                ArtifactRef(
                    path="metrics.json",
                    sha256=canonical_sha256(cast(JsonValue, metrics)),
                    media_type="application/json",
                ),
                ArtifactRef(
                    path="structured-events.jsonl",
                    sha256=sha256(events_bytes).hexdigest(),
                    media_type="application/x-ndjson",
                ),
            ),
            key=lambda item: item.path,
        )
    )
    configuration = ArtifactRef(
        path="config.json",
        sha256=config_sha,
        media_type="application/json",
    )
    manifest = ArtifactManifest(
        artifact_id="phase5-offline-smoke",
        artifact_kind="evaluation_bundle",
        created_at_utc=_AT,
        command_name="spanvouch phase5 offline-smoke",
        code=CodeProvenance(
            git_commit=_CODE_COMMIT,
            repository_identity="local:phase5-offline-smoke",
            dirty_worktree=False,
        ),
        package=PackageProvenance(name="spanvouch", version="0.2.0"),
        contracts={"spanvouch.phase5-offline-smoke": "1.0"},
        configuration=configuration,
        randomness=RandomnessProvenance(
            seed=_SEED,
            deterministic_flags=("fake-provider", "network-disabled"),
        ),
        runtime=RuntimeProvenance(
            python="3.12",
            os="portable",
            architecture="portable",
            dependency_lock_sha256=dependency_lock_sha256,
        ),
        inputs=(configuration,),
        outputs=outputs,
        provider_status="not_used",
    )
    return OfflineAssetRequest(
        destination=destination,
        manifest=manifest,
        config=config,
        metrics=cast(JsonValue, metrics),
        structured_events=events,
        environment=environment,
        readme=readme,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spanvouch phase5 offline-smoke")
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    manifest = asyncio.run(run_offline_acceptance(arguments.output_dir))
    print(manifest.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
