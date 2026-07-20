"""Label-isolated execution and durable accounting for the Phase 5 matrix."""

from __future__ import annotations

import asyncio
import os
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from spanvouch.contracts.versioning import SHA256_PATTERN, canonical_bytes, canonical_sha256
from spanvouch.evaluation.artifacts import (
    capture_owned_directory_identity,
    create_owned_staging_directory,
    delete_owned_staging_directory,
    publish_directory_no_replace,
    quarantine_owned_staging_directory,
    read_verified_directory_tree,
)
from spanvouch.evaluation.corpus import CorpusCell
from spanvouch.evaluation.experiments.config import ConditionId
from spanvouch.evaluation.experiments.models import (
    ConditionPlan,
    ConditionResult,
    ConditionStatus,
    ExperimentFailure,
    ExperimentFailureCategory,
    ExperimentMatrixManifest,
    FailureSource,
    ProviderPlanStatus,
    SelectiveAction,
)
from spanvouch.labs.runtime import FrameworkId

_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_-]*$")


class OutcomeStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    NOT_INVOKED_BY_POLICY = "not_invoked_by_policy"
    PAUSED = "paused"


class PolicyNotInvoked(RuntimeError):
    def __init__(self, code: str) -> None:
        if _SAFE_CODE.fullmatch(code) is None:
            raise ValueError("policy code must be a safe identifier")
        super().__init__(code)
        self.code = code


class ExecutionPaused(RuntimeError):
    def __init__(self, code: str) -> None:
        if _SAFE_CODE.fullmatch(code) is None:
            raise ValueError("pause code must be a safe identifier")
        super().__init__(code)
        self.code = code


class RunnerExecutionError(RuntimeError):
    def __init__(self, category: ExperimentFailureCategory, code: str) -> None:
        if category in {
            ExperimentFailureCategory.DIAGNOSIS,
            ExperimentFailureCategory.VERIFICATION,
        }:
            raise ValueError("post-call failures cannot originate in provider runner")
        if _SAFE_CODE.fullmatch(code) is None:
            raise ValueError("runner failure code must be a safe identifier")
        super().__init__(code)
        self.category = category
        self.code = code


class ConditionExecutor(Protocol):
    """Minimal Task 14 integration seam; adapters own provider-specific context."""

    async def execute(self, plan: ConditionPlan) -> ConditionResult:
        raise NotImplementedError


class ExecutionAdmission(Protocol):
    """All-or-nothing admission for one paid framework pair."""

    def admit(self, plans: tuple[ConditionPlan, ...]) -> bool:
        raise NotImplementedError


class _AllowAllAdmission:
    def admit(self, plans: tuple[ConditionPlan, ...]) -> bool:
        return True


class ProviderPlanOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan: ConditionPlan
    status: OutcomeStatus
    result: ConditionResult | None = None
    terminal_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]*$")

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.status in {OutcomeStatus.COMPLETED, OutcomeStatus.FAILED}:
            if self.result is None or self.terminal_code is not None:
                raise ValueError("executed outcome requires only a condition result")
            expected = (
                OutcomeStatus.COMPLETED
                if self.result.status is ConditionStatus.COMPLETED
                else OutcomeStatus.FAILED
            )
            if self.status is not expected:
                raise ValueError("outcome status does not match condition result")
            _require_result_binding(self.plan, self.result)
        elif self.result is not None or self.terminal_code is None:
            raise ValueError("non-executed outcome requires only a terminal code")
        return self


class ProviderResultEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str = Field(pattern=SHA256_PATTERN)
    cell: CorpusCell
    condition_id: ConditionId
    status: OutcomeStatus
    outcome_sha256: str = Field(pattern=SHA256_PATTERN)
    outcome_path: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        expected = f"results/{self.plan_id}/{self.outcome_sha256}.json"
        if self.outcome_path != expected:
            raise ValueError("provider result path does not match plan and content hash")
        return self


class ProviderPhaseManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: Literal["spanvouch.provider-phase"] = "spanvouch.provider-phase"
    schema_version: Literal["1.0"] = "1.0"
    experiment_id: str = Field(min_length=1)
    experiment_config_sha256: str = Field(pattern=SHA256_PATTERN)
    corpus_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    matrix_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    plan_ids: tuple[str, ...]
    corpus_cells: tuple[CorpusCell, ...]
    eligible_cells: tuple[CorpusCell, ...]
    entries: tuple[ProviderResultEntry, ...]
    status_counts: dict[OutcomeStatus, int]
    missingness_counts: dict[ExperimentFailureCategory, int]
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    total_cost_cny: Decimal = Field(ge=0)
    provider_phase_complete: bool

    @model_validator(mode="after")
    def validate_accounting(self) -> Self:
        if len(self.plan_ids) != len(set(self.plan_ids)):
            raise ValueError("provider manifest plan IDs must be unique")
        if tuple(entry.plan_id for entry in self.entries) != self.plan_ids:
            raise ValueError("provider entries must account for canonical plan order")
        if set(self.status_counts) != set(OutcomeStatus):
            raise ValueError("provider status counts must contain every status")
        if set(self.missingness_counts) != set(ExperimentFailureCategory):
            raise ValueError("provider missingness counts must contain every category")
        actual = Counter(entry.status for entry in self.entries)
        if any(self.status_counts[status] != actual[status] for status in OutcomeStatus):
            raise ValueError("provider status counts do not match entries")
        complete = all(entry.status is not OutcomeStatus.PAUSED for entry in self.entries)
        if self.provider_phase_complete != complete:
            raise ValueError("provider phase completion does not match paused outcomes")
        if len(set(self.corpus_cells)) != len(self.corpus_cells):
            raise ValueError("provider corpus cells must be unique")
        if not set(self.eligible_cells) <= set(self.corpus_cells):
            raise ValueError("eligible cells must belong to provider corpus cells")
        return self


def _model_bytes(model: BaseModel) -> bytes:
    return canonical_bytes(cast(JsonValue, model.model_dump(mode="json")))


class ProviderPhaseRepository:
    """Content-addressed per-plan outcomes plus an atomic final manifest."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=False)
        if os.path.lexists(self.root) and (self.root.is_symlink() or not self.root.is_dir()):
            raise ValueError("provider result root must be a real directory")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "results").mkdir(exist_ok=True)

    def _plan_root(self, plan_id: str) -> Path:
        if re.fullmatch(SHA256_PATTERN, plan_id) is None:
            raise ValueError("plan_id must be a SHA-256 digest")
        return self.root / "results" / plan_id

    def exists(self, plan_id: str) -> bool:
        return os.path.lexists(self._plan_root(plan_id))

    def publish(self, outcome: ProviderPlanOutcome) -> ProviderResultEntry:
        validated = ProviderPlanOutcome.model_validate(outcome.model_dump(mode="python"))
        destination = self._plan_root(validated.plan.plan_id)
        content = _model_bytes(validated)
        digest = sha256(content).hexdigest()
        # Keep the Windows staging prefix short; the final content-addressed
        # plan directory is deliberately 64 characters long.
        staging_target = destination.with_name(f"p-{validated.plan.plan_id[:12]}")
        staging, root_identity = create_owned_staging_directory(staging_target)
        identity = None
        try:
            payload = staging / f"{digest}.json"
            with payload.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            identity = capture_owned_directory_identity(staging)
            publish_directory_no_replace(staging, destination)
            if capture_owned_directory_identity(destination) != identity:
                raise RuntimeError("published provider manifest identity changed")
            if capture_owned_directory_identity(destination) != identity:
                raise RuntimeError("published provider outcome identity changed")
        except Exception:
            if os.path.lexists(staging):
                if identity is None:
                    quarantine_owned_staging_directory(staging, root_identity)
                else:
                    delete_owned_staging_directory(staging, identity)
            raise
        return ProviderResultEntry(
            plan_id=validated.plan.plan_id,
            cell=validated.plan.cell,
            condition_id=validated.plan.condition_id,
            status=validated.status,
            outcome_sha256=digest,
            outcome_path=f"results/{validated.plan.plan_id}/{digest}.json",
        )

    def load(self, plan_id: str) -> ProviderPlanOutcome:
        snapshot = read_verified_directory_tree(self._plan_root(plan_id))
        if snapshot.directories or len(snapshot.files) != 1:
            raise ValueError("provider outcome has unexpected layout")
        relative, content = next(iter(snapshot.files.items()))
        digest = sha256(content).hexdigest()
        if relative != f"{digest}.json":
            raise ValueError("provider outcome content address mismatch")
        outcome = ProviderPlanOutcome.model_validate_json(content)
        if _model_bytes(outcome) != content or outcome.plan.plan_id != plan_id:
            raise ValueError("provider outcome failed canonical plan verification")
        return outcome

    def entry(self, plan_id: str) -> ProviderResultEntry:
        outcome = self.load(plan_id)
        digest = sha256(_model_bytes(outcome)).hexdigest()
        return ProviderResultEntry(
            plan_id=plan_id,
            cell=outcome.plan.cell,
            condition_id=outcome.plan.condition_id,
            status=outcome.status,
            outcome_sha256=digest,
            outcome_path=f"results/{plan_id}/{digest}.json",
        )

    def finalize(self, manifest: ProviderPhaseManifest) -> None:
        validated = ProviderPhaseManifest.model_validate(manifest.model_dump(mode="python"))
        destination = self.root / "manifest"
        content = _model_bytes(validated)
        digest = sha256(content).hexdigest()
        staging, root_identity = create_owned_staging_directory(destination)
        identity = None
        try:
            payload = staging / f"{digest}.json"
            with payload.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            identity = capture_owned_directory_identity(staging)
            publish_directory_no_replace(staging, destination)
        except Exception:
            if os.path.lexists(staging):
                if identity is None:
                    quarantine_owned_staging_directory(staging, root_identity)
                else:
                    delete_owned_staging_directory(staging, identity)
            raise

    @property
    def manifest_sha256(self) -> str:
        snapshot = read_verified_directory_tree(self.root / "manifest")
        if snapshot.directories or len(snapshot.files) != 1:
            raise ValueError("provider manifest has unexpected layout")
        relative, content = next(iter(snapshot.files.items()))
        digest = sha256(content).hexdigest()
        if relative != f"{digest}.json":
            raise ValueError("provider manifest content address mismatch")
        return digest

    def verify(self, *, expected_manifest_sha256: str) -> ProviderPhaseManifest:
        if re.fullmatch(SHA256_PATTERN, expected_manifest_sha256) is None:
            raise ValueError("expected provider manifest hash must be SHA-256")
        snapshot = read_verified_directory_tree(self.root / "manifest")
        if snapshot.directories or len(snapshot.files) != 1:
            raise ValueError("provider manifest has unexpected layout")
        relative, content = next(iter(snapshot.files.items()))
        digest = sha256(content).hexdigest()
        if digest != expected_manifest_sha256 or relative != f"{digest}.json":
            raise ValueError("trusted provider manifest SHA-256 mismatch")
        manifest = ProviderPhaseManifest.model_validate_json(content)
        if _model_bytes(manifest) != content:
            raise ValueError("provider manifest is not canonical JSON")
        actual_plan_dirs = {
            path.name for path in (self.root / "results").iterdir() if path.is_dir()
        }
        if actual_plan_dirs != set(manifest.plan_ids):
            raise ValueError("provider result directory set does not match manifest")
        for expected in manifest.entries:
            if self.entry(expected.plan_id) != expected:
                raise ValueError("provider result entry failed manifest verification")
        return manifest


def _require_result_binding(plan: ConditionPlan, result: ConditionResult) -> None:
    if (
        result.plan_id,
        result.cell,
        result.record_sha256,
        result.trace_sha256,
        result.diagnosis_sha256,
        result.condition_id,
    ) != (
        plan.plan_id,
        plan.cell,
        plan.record_sha256,
        plan.trace_sha256,
        plan.diagnosis_sha256,
        plan.condition_id,
    ):
        raise ValueError("condition result does not match its exact plan")
    if result.status is ConditionStatus.FAILED and (
        result.selective_action is SelectiveAction.ACCEPT
    ):
        raise ValueError("failed condition cannot be accepted")


class ExperimentRunner:
    def __init__(
        self,
        *,
        executor: ConditionExecutor,
        admission: ExecutionAdmission | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._executor = executor
        self._admission = admission or _AllowAllAdmission()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run_provider_phase(
        self,
        *,
        plans: tuple[ConditionPlan, ...],
        matrix: ExperimentMatrixManifest,
        repository: ProviderPhaseRepository,
    ) -> ProviderPhaseManifest:
        validated_matrix = ExperimentMatrixManifest.model_validate(
            matrix.model_dump(mode="python")
        )
        validated_plans = tuple(
            ConditionPlan.model_validate(plan.model_dump(mode="python")) for plan in plans
        )
        if tuple(plan.plan_id for plan in validated_plans) != validated_matrix.plan_ids:
            raise ValueError("runner plans do not match canonical matrix plan IDs")
        for plan in validated_plans:
            if (
                plan.experiment_id != validated_matrix.experiment_id
                or plan.experiment_config_sha256
                != validated_matrix.experiment_config_sha256
                or plan.corpus_manifest_sha256
                != validated_matrix.corpus_manifest_sha256
            ):
                raise ValueError("runner plan parent hashes do not match matrix")
        if os.path.lexists(repository.root / "manifest"):
            existing_manifest = repository.verify(
                expected_manifest_sha256=repository.manifest_sha256
            )
            matrix_sha256 = canonical_sha256(
                cast(JsonValue, validated_matrix.model_dump(mode="json"))
            )
            expected_identity = (
                validated_matrix.experiment_id,
                validated_matrix.experiment_config_sha256,
                validated_matrix.corpus_manifest_sha256,
                validated_matrix.candidate_manifest_sha256,
                matrix_sha256,
                validated_matrix.plan_ids,
            )
            actual_identity = (
                existing_manifest.experiment_id,
                existing_manifest.experiment_config_sha256,
                existing_manifest.corpus_manifest_sha256,
                existing_manifest.candidate_manifest_sha256,
                existing_manifest.matrix_manifest_sha256,
                existing_manifest.plan_ids,
            )
            if actual_identity != expected_identity:
                raise ValueError("existing provider manifest belongs to another matrix")
            return existing_manifest

        groups = _execution_groups(validated_plans)
        outcomes: dict[str, ProviderPlanOutcome] = {}
        for group in groups:
            existing_outcomes = {
                plan.plan_id: repository.load(plan.plan_id)
                for plan in group
                if repository.exists(plan.plan_id)
            }
            outcomes.update(existing_outcomes)
            missing = tuple(
                plan for plan in group if plan.plan_id not in existing_outcomes
            )
            if not missing:
                continue
            if (
                group[0].provider_status is ProviderPlanStatus.REQUIRED
                and not self._admission.admit(missing)
            ):
                for plan in missing:
                    outcome = ProviderPlanOutcome(
                        plan=plan,
                        status=OutcomeStatus.PAUSED,
                        terminal_code="budget-paused",
                    )
                    repository.publish(outcome)
                    outcomes[plan.plan_id] = outcome
                continue
            for index, plan in enumerate(missing):
                outcome = await self._execute(plan)
                repository.publish(outcome)
                outcomes[outcome.plan.plan_id] = outcome
                if (
                    outcome.status is OutcomeStatus.PAUSED
                    and plan.provider_status is ProviderPlanStatus.REQUIRED
                ):
                    for paired_plan in missing[index + 1 :]:
                        paired_outcome = ProviderPlanOutcome(
                            plan=paired_plan,
                            status=OutcomeStatus.PAUSED,
                            terminal_code=outcome.terminal_code,
                        )
                        repository.publish(paired_outcome)
                        outcomes[paired_plan.plan_id] = paired_outcome
                    break

        ordered_outcomes = tuple(outcomes[plan.plan_id] for plan in validated_plans)
        entries = tuple(repository.entry(plan.plan_id) for plan in validated_plans)
        results = tuple(
            outcome.result for outcome in ordered_outcomes if outcome.result is not None
        )
        missingness: Counter[ExperimentFailureCategory] = Counter()
        for result in results:
            if result.failure is not None:
                missingness[result.failure.category] += 1
        status_counts = Counter(entry.status for entry in entries)
        corpus_cells = tuple(
            sorted(
                (
                    *validated_matrix.eligible_cells,
                    *(item.cell for item in validated_matrix.ineligible),
                ),
                key=lambda cell: cell.sort_key(),
            )
        )
        manifest = ProviderPhaseManifest(
            experiment_id=validated_matrix.experiment_id,
            experiment_config_sha256=validated_matrix.experiment_config_sha256,
            corpus_manifest_sha256=validated_matrix.corpus_manifest_sha256,
            candidate_manifest_sha256=validated_matrix.candidate_manifest_sha256,
            matrix_manifest_sha256=canonical_sha256(
                cast(JsonValue, validated_matrix.model_dump(mode="json"))
            ),
            plan_ids=validated_matrix.plan_ids,
            corpus_cells=corpus_cells,
            eligible_cells=validated_matrix.eligible_cells,
            entries=entries,
            status_counts={status: status_counts[status] for status in OutcomeStatus},
            missingness_counts={
                category: missingness[category] for category in ExperimentFailureCategory
            },
            total_input_tokens=sum(
                result.usage.input_tokens
                for result in results
                if result.usage is not None
            ),
            total_output_tokens=sum(
                result.usage.output_tokens
                for result in results
                if result.usage is not None
            ),
            total_cost_cny=sum(
                (result.cost_cny or Decimal("0") for result in results),
                start=Decimal("0"),
            ),
            provider_phase_complete=all(
                outcome.status is not OutcomeStatus.PAUSED
                for outcome in ordered_outcomes
            ),
        )
        repository.finalize(manifest)
        return manifest

    async def _execute(self, plan: ConditionPlan) -> ProviderPlanOutcome:
        try:
            result = await self._executor.execute(plan)
            result = ConditionResult.model_validate(result.model_dump(mode="python"))
            _require_result_binding(plan, result)
        except PolicyNotInvoked as error:
            return ProviderPlanOutcome(
                plan=plan,
                status=OutcomeStatus.NOT_INVOKED_BY_POLICY,
                terminal_code=error.code,
            )
        except ExecutionPaused as error:
            return ProviderPlanOutcome(
                plan=plan, status=OutcomeStatus.PAUSED, terminal_code=error.code
            )
        except asyncio.CancelledError:
            return ProviderPlanOutcome(
                plan=plan,
                status=OutcomeStatus.FAILED,
                result=self._failed_result(
                    plan, ExperimentFailureCategory.INFRASTRUCTURE, "cancelled"
                ),
            )
        except RunnerExecutionError as error:
            return ProviderPlanOutcome(
                plan=plan,
                status=OutcomeStatus.FAILED,
                result=self._failed_result(plan, error.category, error.code),
            )
        except (TypeError, ValueError):
            return ProviderPlanOutcome(
                plan=plan,
                status=OutcomeStatus.FAILED,
                result=self._failed_result(
                    plan, ExperimentFailureCategory.CONTRACT_INVALID, "invalid-result"
                ),
            )
        return ProviderPlanOutcome(
            plan=plan,
            status=(
                OutcomeStatus.COMPLETED
                if result.status is ConditionStatus.COMPLETED
                else OutcomeStatus.FAILED
            ),
            result=result,
        )

    def _failed_result(
        self,
        plan: ConditionPlan,
        category: ExperimentFailureCategory,
        code: str,
    ) -> ConditionResult:
        now = self._clock()
        return ConditionResult(
            plan_id=plan.plan_id,
            cell=plan.cell,
            record_sha256=plan.record_sha256,
            trace_sha256=plan.trace_sha256,
            diagnosis_sha256=plan.diagnosis_sha256,
            condition_id=plan.condition_id,
            status=ConditionStatus.FAILED,
            selective_action=SelectiveAction.ABSTAIN,
            verifier_report_sha256s=(),
            request_audit_sha256s=(),
            usage=None,
            cost_cny=None,
            cache_status="failed",
            started_at_utc=now,
            completed_at_utc=now,
            failure=ExperimentFailure(
                category=category,
                code=code,
                source=FailureSource.PROVIDER_RUNNER,
            ),
        )


def _execution_groups(plans: tuple[ConditionPlan, ...]) -> tuple[tuple[ConditionPlan, ...], ...]:
    provider_pairs: dict[tuple[str, ConditionId], list[ConditionPlan]] = defaultdict(list)
    indexed_groups: list[tuple[int, tuple[ConditionPlan, ...]]] = []
    for index, plan in enumerate(plans):
        if plan.provider_status is ProviderPlanStatus.NOT_REQUIRED:
            indexed_groups.append((index, (plan,)))
        else:
            provider_pairs[(plan.cell.pair_identity, plan.condition_id)].append(plan)
    for pair in provider_pairs.values():
        if len(pair) != 2 or {item.cell.framework_id for item in pair} != {
            FrameworkId.AUTOGEN,
            FrameworkId.LANGGRAPH,
        }:
            raise ValueError("paid execution requires complete framework pairs")
        indexed_groups.append(
            (min(plans.index(item) for item in pair), tuple(pair))
        )
    return tuple(group for _, group in sorted(indexed_groups, key=lambda item: item[0]))
