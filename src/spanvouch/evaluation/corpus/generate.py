"""Offline Stage A planning and generation for the Phase 5 trace corpus."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.corpus.models import (
    CorpusCell,
    CorpusManifest,
    CorpusManifestMetadata,
    Phase5CorpusPlan,
)
from spanvouch.evaluation.corpus.repository import TraceReplayRepository
from spanvouch.evaluation.experiments import Phase5ExperimentConfig
from spanvouch.labs.opslab.templates import build_opslab_templates
from spanvouch.labs.runtime import (
    AgentRuntimeAdapter,
    ExecutionProvenance,
    ExecutionRecord,
    FrameworkId,
    LabScenario,
    ParityResult,
    RuntimeConfig,
    ScenarioParityValidator,
    logical_execution_payload,
)
from spanvouch.labs.supportlab.runtime import build_support_lab_scenarios

_FRAMEWORK_ORDER = (FrameworkId.LANGGRAPH, FrameworkId.AUTOGEN)


@dataclass(frozen=True)
class CorpusPlanCell:
    """One framework execution in a paired, deterministic Stage A plan."""

    scenario: LabScenario
    framework_id: FrameworkId
    repetition: int
    seed: int

    @property
    def identity(self) -> str:
        return ":".join(
            (
                self.scenario.domain,
                self.scenario.template_id,
                self.scenario.scenario_id,
                self.framework_id.value,
                str(self.repetition),
                str(self.seed),
            )
        )


@dataclass(frozen=True)
class CorpusGenerationResult:
    """Frozen corpus plus the pairwise parity outcomes used to create it."""

    repository: TraceReplayRepository
    manifest: CorpusManifest
    parity_results: tuple[ParityResult, ...]
    logical_payload_sha256: str

    @property
    def has_unapproved_parity_mismatches(self) -> bool:
        return any(result.status == "mismatched" for result in self.parity_results)


def build_corpus_plan(config: Phase5ExperimentConfig) -> tuple[CorpusPlanCell, ...]:
    """Build all scenario/repetition pairs without condition or model metadata."""
    config = Phase5ExperimentConfig.model_validate(config.model_dump(mode="json"))
    scenarios = (
        *build_support_lab_scenarios(config.seed),
        *(template.to_lab_scenario() for template in build_opslab_templates()),
    )
    cells: list[CorpusPlanCell] = []
    for scenario_index, scenario in enumerate(scenarios):
        for repetition in range(1, config.repetitions + 1):
            seed = config.seed + scenario_index * config.repetitions + repetition - 1
            cells.extend(
                CorpusPlanCell(
                    scenario=scenario,
                    framework_id=framework_id,
                    repetition=repetition,
                    seed=seed,
                )
                for framework_id in _FRAMEWORK_ORDER
            )
    return tuple(cells)


async def generate_phase5_corpus(
    *,
    config: Phase5ExperimentConfig,
    destination: Path,
    adapters: Mapping[FrameworkId, AgentRuntimeAdapter],
    provenance: ExecutionProvenance,
    created_at_utc: datetime | None = None,
) -> CorpusGenerationResult:
    """Execute every matched pair once and publish one immutable corpus."""
    config = Phase5ExperimentConfig.model_validate(config.model_dump(mode="json"))
    if destination.exists():
        raise FileExistsError("corpus destination must not already exist")
    if config.mode.value == "formal":
        if config.config_sha256 is None or config.frozen_at_utc is None:
            raise ValueError("formal corpus generation requires a frozen configuration")
        if provenance.dirty_worktree:
            raise ValueError("formal corpus generation requires a clean worktree")
    if set(adapters) != set(_FRAMEWORK_ORDER):
        raise ValueError("Stage A requires exactly the LangGraph and AutoGen adapters")

    records: list[ExecutionRecord] = []
    parity_results: list[ParityResult] = []
    plan = build_corpus_plan(config)
    validator = ScenarioParityValidator()
    for left, right in zip(plan[::2], plan[1::2], strict=True):
        pair: list[ExecutionRecord] = []
        for cell in (left, right):
            run_config = RuntimeConfig(
                seed=cell.seed,
                repetition=cell.repetition,
                max_steps=8,
                timeout_seconds=5.0,
                max_retries=0,
                max_tool_calls=8,
            )
            record = await adapters[cell.framework_id].execute(cell.scenario, run_config)
            _require_record_matches_cell(record, cell)
            _require_record_provenance(record, provenance)
            pair.append(record)
            records.append(record)
        parity_results.append(validator.validate(pair[0], pair[1]))

    frozen_parity = tuple(parity_results)
    experiment_config_sha256 = canonical_sha256(config.model_dump(mode="json"))
    ordered_cells = tuple(
        CorpusCell(
            domain=cell.scenario.domain,
            template_id=cell.scenario.template_id,
            scenario_id=cell.scenario.scenario_id,
            framework_id=cell.framework_id,
            repetition=cell.repetition,
            seed=cell.seed,
        )
        for cell in plan
    )
    phase5_plan = Phase5CorpusPlan.from_cells(
        mode=config.mode.value,
        repetitions=config.repetitions,
        experiment_config_sha256=experiment_config_sha256,
        ordered_cells=ordered_cells,
    )
    metadata = CorpusManifestMetadata(
        corpus_id=f"phase5-{config.mode.value}",
        mode=config.mode.value,
        experiment_config_sha256=experiment_config_sha256,
        git_commit=provenance.git_commit,
        dependency_lock_sha256=provenance.dependency_lock_sha256,
        dataset_manifest_sha256=provenance.dataset_manifest_sha256,
        dirty_worktree=provenance.dirty_worktree,
        expected_cell_count=len(plan),
        expected_pair_count=len(plan) // 2,
        phase5_plan=phase5_plan,
        created_at_utc=created_at_utc or datetime.now(UTC),
        parity_results_sha256=canonical_sha256(
            cast(
                JsonValue,
                [result.model_dump(mode="json") for result in frozen_parity],
            )
        ),
    )
    repository = TraceReplayRepository.freeze(
        records=records,
        parity_results=frozen_parity,
        destination=destination,
        manifest_metadata=metadata,
    )
    manifest = repository.verify()
    logical_payload_sha256 = canonical_sha256(
        {
            "metadata": metadata.model_dump(
                mode="json",
                exclude={"created_at_utc", "git_commit"},
            ),
            "entries": [
                {
                    "cell": CorpusCell(
                        domain=record.domain,
                        template_id=record.template_id,
                        scenario_id=record.scenario_id,
                        framework_id=record.framework_id,
                        repetition=record.repetition,
                        seed=record.seed,
                    ).model_dump(mode="json"),
                    "execution": logical_execution_payload(record),
                }
                for record in records
            ],
        }
    )
    return CorpusGenerationResult(
        repository=repository,
        manifest=manifest,
        parity_results=frozen_parity,
        logical_payload_sha256=logical_payload_sha256,
    )


def _require_record_matches_cell(
    record: ExecutionRecord, cell: CorpusPlanCell
) -> None:
    if (
        record.domain != cell.scenario.domain
        or record.template_id != cell.scenario.template_id
        or record.scenario_id != cell.scenario.scenario_id
        or record.framework_id is not cell.framework_id
        or record.repetition != cell.repetition
        or record.seed != cell.seed
    ):
        raise ValueError("adapter execution record does not match its corpus cell")


def _require_record_provenance(
    record: ExecutionRecord,
    expected: ExecutionProvenance,
) -> None:
    actual = record.provenance
    if actual.dirty_worktree or (
        actual.git_commit,
        actual.dependency_lock_sha256,
        actual.dataset_manifest_sha256,
        actual.dirty_worktree,
    ) != (
        expected.git_commit,
        expected.dependency_lock_sha256,
        expected.dataset_manifest_sha256,
        expected.dirty_worktree,
    ):
        raise ValueError("execution record provenance does not match corpus provenance")
