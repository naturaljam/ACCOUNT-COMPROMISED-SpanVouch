"""Immutable research records for the Phase 5 verification matrix."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from spanvouch.contracts.diagnosis import (
    ClaimStage,
    DiagnosisReport,
    DiagnosisStatus,
    ProviderUsage,
)
from spanvouch.contracts.verification import FindingCode, VerifierReport, VerifierVerdict
from spanvouch.contracts.versioning import SHA256_PATTERN, canonical_sha256
from spanvouch.evaluation.artifacts import require_safe_artifact_content
from spanvouch.evaluation.corpus import CorpusCell
from spanvouch.evaluation.experiments.config import (
    ConditionId,
    ModelEndpointConfig,
    Phase5ExperimentConfig,
)
from spanvouch.evaluation.experiments.diagnosis import FrozenDiagnosisCandidate


class ExperimentFailureCategory(StrEnum):
    FRAMEWORK_EXECUTION = "framework_execution_failure"
    FRAMEWORK_INCOMPATIBILITY = "framework_incompatibility"
    INFRASTRUCTURE = "infrastructure_failure"
    PROVIDER = "provider_failure"
    CONTRACT_INVALID = "contract_invalid"
    DIAGNOSIS = "diagnosis_error"
    VERIFICATION = "verification_error"


class ConditionStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    NOT_INVOKED_BY_POLICY = "not_invoked_by_policy"


class SelectiveAction(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    REPLAN = "replan"
    ABSTAIN = "abstain"
    REVIEW = "review_required"


class FailureSource(StrEnum):
    PROVIDER_RUNNER = "provider_runner"
    POST_CALL_EVALUATOR = "post_call_evaluator"


class ProviderPlanStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"


class VerifierEvaluationEvidence(BaseModel):
    """Parsed verifier facts safe to persist outside raw provider responses."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    verdict: VerifierVerdict
    finding_codes: tuple[FindingCode, ...]
    selectors: tuple[str, ...]


class ConditionEvaluationEvidence(BaseModel):
    """Self-hashed label-free projection used only by the post-call evaluator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    diagnosis_report_sha256: str = Field(pattern=SHA256_PATTERN)
    diagnosis_status: DiagnosisStatus
    diagnosis_family: str | None = None
    causal_stages: tuple[ClaimStage, ...]
    causal_tokens: tuple[str, ...]
    diagnosis_selectors: tuple[str, ...]
    verifier_reports: tuple[VerifierEvaluationEvidence, ...]
    projection_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        payload = cast(
            JsonValue,
            self.model_dump(mode="json", exclude={"projection_sha256"}),
        )
        if canonical_sha256(payload) != self.projection_sha256:
            raise ValueError("evaluation evidence projection hash mismatch")
        if self.causal_tokens != tuple(dict.fromkeys(self.causal_tokens)) or any(
            re.fullmatch(r"[a-z][a-z0-9_]*", token) is None
            for token in self.causal_tokens
        ):
            raise ValueError("causal tokens must be unique safe identifiers")
        if self.diagnosis_selectors != tuple(sorted(set(self.diagnosis_selectors))):
            raise ValueError("diagnosis selectors must be sorted and unique")
        return self

    @classmethod
    def from_reports(
        cls,
        diagnosis: DiagnosisReport,
        verifier_reports: tuple[VerifierReport, ...],
    ) -> ConditionEvaluationEvidence:
        token_groups = tuple(
            tuple(re.findall(r"[a-z][a-z0-9_]*", claim.statement.casefold()))
            for claim in diagnosis.causal_chain
        )
        if any(not group for group in token_groups):
            raise ValueError("diagnosis claim cannot be projected to safe causal tokens")
        tokens = tuple(dict.fromkeys(token for group in token_groups for token in group))
        projected_verifiers = tuple(
            VerifierEvaluationEvidence(
                artifact_sha256=canonical_sha256(report),
                verdict=report.verdict,
                finding_codes=tuple(finding.code for finding in report.findings),
                selectors=tuple(
                    sorted(
                        {
                            selector
                            for finding in report.findings
                            for selector in finding.related_selectors
                        }
                        | {
                            selector
                            for gap in report.evidence_gaps
                            for selector in gap.allowed_selectors
                        }
                    )
                ),
            )
            for report in verifier_reports
        )
        payload: dict[str, object] = {
            "diagnosis_report_sha256": canonical_sha256(diagnosis),
            "diagnosis_status": diagnosis.status,
            "diagnosis_family": diagnosis.failure_type,
            "causal_stages": tuple(claim.stage for claim in diagnosis.causal_chain),
            "causal_tokens": tokens,
            "diagnosis_selectors": tuple(
                sorted({evidence.canonical for evidence in diagnosis.evidence})
            ),
            "verifier_reports": projected_verifiers,
        }
        require_safe_artifact_content(
            "condition_evidence",
            (
                *tokens,
                *(evidence.canonical for evidence in diagnosis.evidence),
                *(
                    selector
                    for report in projected_verifiers
                    for selector in report.selectors
                ),
            ),
        )
        projection_payload = cast(
            JsonValue,
            {
                "diagnosis_report_sha256": canonical_sha256(diagnosis),
                "diagnosis_status": diagnosis.status.value,
                "diagnosis_family": diagnosis.failure_type,
                "causal_stages": [claim.stage.value for claim in diagnosis.causal_chain],
                "causal_tokens": list(tokens),
                "diagnosis_selectors": sorted(
                    {evidence.canonical for evidence in diagnosis.evidence}
                ),
                "verifier_reports": [
                    report.model_dump(mode="json") for report in projected_verifiers
                ],
            },
        )
        projection_hash = canonical_sha256(projection_payload)
        return cls.model_validate({**payload, "projection_sha256": projection_hash})


class ExperimentFailure(BaseModel):
    """Typed failure whose source cannot be confused with evaluated correctness."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: ExperimentFailureCategory
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    source: FailureSource
    evaluator_provenance_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    is_correct: bool | None = None

    @model_validator(mode="after")
    def validate_failure_semantics(self) -> Self:
        evaluated = {
            ExperimentFailureCategory.DIAGNOSIS,
            ExperimentFailureCategory.VERIFICATION,
        }
        if self.category not in evaluated and self.is_correct is not None:
            raise ValueError("operational failure cannot carry correctness")
        if self.category in evaluated:
            if self.source is not FailureSource.POST_CALL_EVALUATOR:
                raise ValueError("diagnosis/verification errors require post-call evaluator source")
            if self.evaluator_provenance_sha256 is None:
                raise ValueError("diagnosis/verification errors require evaluator provenance")
        return self


class ConditionPlan(BaseModel):
    """One deterministic condition execution bound to a frozen diagnosis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str = Field(pattern=SHA256_PATTERN)
    experiment_id: str = Field(min_length=1)
    experiment_config_sha256: str = Field(pattern=SHA256_PATTERN)
    corpus_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    cell: CorpusCell
    record_sha256: str = Field(pattern=SHA256_PATTERN)
    trace_sha256: str = Field(pattern=SHA256_PATTERN)
    diagnosis_sha256: str = Field(pattern=SHA256_PATTERN)
    condition_id: ConditionId
    prompt_version: str = Field(min_length=1)
    provider_status: ProviderPlanStatus
    provider: str | None = None
    model: str | None = None
    generation: ModelEndpointConfig | None = None

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="python", exclude={"plan_id"})

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        if self.provider_status is ProviderPlanStatus.NOT_REQUIRED:
            if self.provider is not None or self.model is not None or self.generation is not None:
                raise ValueError("provider-not-required plan must not bind provider generation")
        else:
            if self.provider is None or self.model is None or self.generation is None:
                raise ValueError("provider-required plan must bind provider generation")
            if (
                self.provider != self.generation.provider
                or self.model != self.generation.model
                or self.prompt_version != self.generation.prompt_version
            ):
                raise ValueError("provider plan fields drift from generation configuration")
        expected = canonical_sha256(cast(JsonValue, self.identity_payload()))
        if self.plan_id != expected:
            raise ValueError("plan_id does not match causal plan inputs")
        return self

    @classmethod
    def from_payload(cls, **payload: Any) -> ConditionPlan:
        if "plan_id" in payload:
            raise ValueError("plan_id is derived and cannot be supplied")
        validated_payload = dict(payload)
        plan_id = canonical_sha256(cast(JsonValue, validated_payload))
        return cls.model_validate({"plan_id": plan_id, **validated_payload})


class ConditionResult(BaseModel):
    """Label-free output of one condition execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str = Field(pattern=SHA256_PATTERN)
    cell: CorpusCell
    record_sha256: str = Field(pattern=SHA256_PATTERN)
    trace_sha256: str = Field(pattern=SHA256_PATTERN)
    diagnosis_sha256: str = Field(pattern=SHA256_PATTERN)
    condition_id: ConditionId
    status: ConditionStatus
    selective_action: SelectiveAction
    verifier_report_sha256s: tuple[str, ...]
    request_audit_sha256s: tuple[str, ...]
    evaluation_evidence: ConditionEvaluationEvidence | None = None
    usage: ProviderUsage | None = None
    cost_cny: Decimal | None = Field(default=None, ge=0)
    cache_status: Literal[
        "not_required", "not_invoked_by_policy", "hit", "miss", "failed"
    ]
    started_at_utc: datetime
    completed_at_utc: datetime
    failure: ExperimentFailure | None = None

    @field_validator("started_at_utc", "completed_at_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("condition timestamps must be UTC")
        return value

    @field_validator("verifier_report_sha256s", "request_audit_sha256s")
    @classmethod
    def require_unique_hashes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(re.fullmatch(SHA256_PATTERN, item) is None for item in value):
            raise ValueError("condition artifact hashes must be SHA-256 values")
        if len(value) != len(set(value)):
            raise ValueError("condition artifact hashes must be unique")
        return value

    @model_validator(mode="after")
    def validate_result_state(self) -> Self:
        if self.completed_at_utc < self.started_at_utc:
            raise ValueError("condition completion precedes start")
        if self.status is ConditionStatus.FAILED and self.failure is None:
            raise ValueError("failed condition requires typed failure")
        if self.status is ConditionStatus.COMPLETED and self.failure is not None:
            raise ValueError("completed condition cannot contain failure")
        if self.status is ConditionStatus.NOT_INVOKED_BY_POLICY and (
            self.failure is not None or self.cache_status != "not_invoked_by_policy"
        ):
            raise ValueError(
                "policy-skipped condition must be failure-free and explicitly cached"
            )
        if self.usage is not None and self.usage.request_id is not None:
            raise ValueError("condition usage must not retain a raw request ID")
        if self.evaluation_evidence is not None and (
            self.evaluation_evidence.diagnosis_report_sha256 != self.diagnosis_sha256
            or tuple(
                report.artifact_sha256
                for report in self.evaluation_evidence.verifier_reports
            )
            != self.verifier_report_sha256s
        ):
            raise ValueError("condition evaluation evidence does not bind result hashes")
        return self


class IneligibleCell(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cell: CorpusCell
    category: ExperimentFailureCategory
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")


class ExperimentMatrixManifest(BaseModel):
    """Complete, label-isolated identity of the planned experiment matrix."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: Literal["spanvouch.experiment-matrix-manifest"] = (
        "spanvouch.experiment-matrix-manifest"
    )
    schema_version: Literal["1.0"] = "1.0"
    experiment_id: str = Field(min_length=1)
    experiment_config_sha256: str = Field(pattern=SHA256_PATTERN)
    corpus_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    plan_ids: tuple[str, ...]
    eligible_cells: tuple[CorpusCell, ...]
    ineligible: tuple[IneligibleCell, ...]
    eligible_cell_count: int = Field(ge=0)
    ineligible_cell_count: int = Field(ge=0)
    condition_counts: dict[ConditionId, int]

    @model_validator(mode="after")
    def validate_complete_identity(self) -> Self:
        if len(self.plan_ids) != len(set(self.plan_ids)):
            raise ValueError("matrix plan IDs must be unique")
        if any(re.fullmatch(SHA256_PATTERN, item) is None for item in self.plan_ids):
            raise ValueError("matrix plan IDs must be SHA-256 values")
        if self.eligible_cell_count != len(self.eligible_cells):
            raise ValueError("eligible cell count does not match cells")
        if self.ineligible_cell_count != len(self.ineligible):
            raise ValueError("ineligible cell count does not match cells")
        if len(set(self.eligible_cells)) != len(self.eligible_cells):
            raise ValueError("eligible cells must be unique")
        ineligible_cells = tuple(item.cell for item in self.ineligible)
        if len(set(ineligible_cells)) != len(ineligible_cells):
            raise ValueError("ineligible cells must be unique")
        if set(self.eligible_cells).intersection(ineligible_cells):
            raise ValueError("eligible and ineligible cells must be disjoint")
        if set(self.condition_counts) != set(ConditionId):
            raise ValueError("condition counts must contain all six conditions")
        if any(count != self.eligible_cell_count for count in self.condition_counts.values()):
            raise ValueError("every condition count must match eligible cells")
        if len(self.plan_ids) != self.eligible_cell_count * len(ConditionId):
            raise ValueError("matrix must contain six plan IDs per eligible cell")
        return self

    @classmethod
    def from_plans(
        cls,
        *,
        plans: tuple[ConditionPlan, ...],
        candidates: tuple[FrozenDiagnosisCandidate, ...],
        config: Phase5ExperimentConfig,
        candidate_manifest_sha256: str,
        ineligible: tuple[IneligibleCell, ...],
        expected_cells: tuple[CorpusCell, ...],
    ) -> ExperimentMatrixManifest:
        if not candidates:
            raise ValueError("matrix requires eligible candidates")
        from spanvouch.evaluation.experiments.planner import VerificationMatrixPlanner

        VerificationMatrixPlanner().validate(
            plans,
            candidates,
            config,
            expected_cells=expected_cells,
            ineligible=ineligible,
        )
        corpus_hashes = {candidate.corpus_manifest_sha256 for candidate in candidates}
        if len(corpus_hashes) != 1:
            raise ValueError("eligible candidates must share one corpus manifest")
        eligible_cells = tuple(
            sorted((candidate.cell for candidate in candidates), key=lambda cell: cell.sort_key())
        )
        counts = {condition: 0 for condition in ConditionId}
        for plan in plans:
            counts[plan.condition_id] += 1
        return cls(
            experiment_id=config.experiment_id,
            experiment_config_sha256=canonical_sha256(
                cast(JsonValue, config.model_dump(mode="json"))
            ),
            corpus_manifest_sha256=next(iter(corpus_hashes)),
            candidate_manifest_sha256=candidate_manifest_sha256,
            plan_ids=tuple(plan.plan_id for plan in plans),
            eligible_cells=eligible_cells,
            ineligible=ineligible,
            eligible_cell_count=len(eligible_cells),
            ineligible_cell_count=len(ineligible),
            condition_counts=counts,
        )
