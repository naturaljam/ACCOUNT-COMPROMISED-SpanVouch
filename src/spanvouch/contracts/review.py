from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from spanvouch.contracts.diagnosis import (
    AbstainReason,
    ClaimStage,
    DiagnoserKind,
    DiagnosisProvenance,
    DiagnosisReport,
    DiagnosisStatus,
    EvidenceSelector,
)
from spanvouch.contracts.verification import (
    VerificationMode,
    VerifierKind,
    VerifierReport,
    VerifierVerdict,
)
from spanvouch.contracts.versioning import (
    IDENTIFIER_PATTERN,
    SHA256_PATTERN,
    ContractModel,
    ContractRoot,
    canonical_sha256,
)
from spanvouch.failure_types import SUPPORTED_DIAGNOSIS_FAILURE_TYPES, FailureType


class ReviewStatus(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    VERIFYING = "verifying"
    REVISION_REQUESTED = "revision_requested"
    REVISING = "revising"
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    REJECTED = "rejected"


class DecisionAction(StrEnum):
    CONFIRM = "confirm"
    CORRECT = "correct"
    REJECT = "reject"


class RevisionOrigin(StrEnum):
    INITIAL_DIAGNOSIS = "initial_diagnosis"
    EVIDENCE_REVISION = "evidence_revision"
    HUMAN_CORRECTION = "human_correction"


class WorkflowEventType(StrEnum):
    CASE_CREATED = "case_created"
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION_COMPLETED = "verification_completed"
    REVISION_REQUESTED = "revision_requested"
    REVISION_STARTED = "revision_started"
    REVISION_COMPLETED = "revision_completed"
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"
    HUMAN_CONFIRMED = "human_confirmed"
    HUMAN_CORRECTED = "human_corrected"
    HUMAN_REJECTED = "human_rejected"
    PROVIDER_FAILED = "provider_failed"
    REVISION_PROVIDER_FAILED = "revision_provider_failed"


def _validate_sorted_unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{field_name} must be sorted and unique")
    return values


class DiagnosisRevision(ContractModel):
    revision_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    revision_number: int = Field(ge=0)
    origin: RevisionOrigin
    previous_report_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    report: DiagnosisReport
    report_sha256: str = Field(pattern=SHA256_PATTERN)
    triggering_gap_ids: tuple[str, ...] = ()
    provenance: DiagnosisProvenance
    created_at: datetime

    @field_validator("triggering_gap_ids")
    @classmethod
    def validate_gap_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_sorted_unique(values, "triggering_gap_ids")

    @model_validator(mode="after")
    def validate_revision_chain(self) -> Self:
        if self.report_sha256 != canonical_sha256(self.report):
            raise ValueError("report_sha256 does not match report")
        if self.provenance != self.report.provenance:
            raise ValueError("revision provenance must match report provenance")
        if self.revision_number == 0:
            if self.origin is not RevisionOrigin.INITIAL_DIAGNOSIS:
                raise ValueError("revision zero must be the initial diagnosis")
            if self.previous_report_sha256 is not None or self.triggering_gap_ids:
                raise ValueError("revision zero has no previous report or triggering gaps")
        else:
            if self.origin is RevisionOrigin.INITIAL_DIAGNOSIS:
                raise ValueError("later revisions cannot be initial diagnoses")
            if self.previous_report_sha256 is None:
                raise ValueError("later revisions require previous_report_sha256")
            if self.origin is RevisionOrigin.EVIDENCE_REVISION:
                if self.revision_number != 1:
                    raise ValueError("evidence revision is permanently capped at revision one")
                if not self.triggering_gap_ids:
                    raise ValueError("evidence revisions require triggering_gap_ids")
        return self


class CorrectionClaim(ContractModel):
    stage: ClaimStage
    statement: str = Field(min_length=1)
    selectors: tuple[EvidenceSelector, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_selectors(self) -> Self:
        canonical = tuple(selector.canonical for selector in self.selectors)
        if canonical != tuple(sorted(set(canonical))):
            raise ValueError("claim selectors must be sorted and unique")
        return self


class DiagnosisCorrectionDraft(ContractModel):
    status: DiagnosisStatus
    failure_type: FailureType | None = None
    critical_span_ids: tuple[str, ...] = ()
    causal_chain: tuple[CorrectionClaim, ...] = Field(default=(), max_length=3)
    confidence: float = Field(ge=0.0, le=1.0)
    abstain_reason: AbstainReason | None = None

    @model_validator(mode="after")
    def validate_diagnosis(self) -> Self:
        if len(self.critical_span_ids) != len(set(self.critical_span_ids)):
            raise ValueError("critical_span_ids must be unique")
        if self.status is DiagnosisStatus.DIAGNOSED:
            if self.failure_type not in SUPPORTED_DIAGNOSIS_FAILURE_TYPES:
                raise ValueError("diagnosed status requires a supported failure type")
            if not self.critical_span_ids or not self.causal_chain:
                raise ValueError("diagnosed status requires critical spans and claims")
            if self.abstain_reason is not None:
                raise ValueError("diagnosed status forbids abstain_reason")
        elif self.status is DiagnosisStatus.NO_FAILURE:
            if self.failure_type is not FailureType.NO_FAILURE:
                raise ValueError("no_failure status requires no_failure type")
            if self.critical_span_ids or self.causal_chain or self.abstain_reason is not None:
                raise ValueError("no_failure status forbids failure details")
        else:
            if self.failure_type is not None:
                raise ValueError("abstained status forbids failure_type")
            if self.abstain_reason is None:
                raise ValueError("abstained status requires abstain_reason")
            if self.critical_span_ids or self.causal_chain:
                raise ValueError("abstained status forbids failure details")
        return self


class HumanDecisionDraft(ContractModel):
    action: DecisionAction
    expected_version: int = Field(ge=0)
    reviewer_label: str = Field(min_length=1, max_length=100)
    reason: str | None = Field(default=None, max_length=1000)
    correction: DiagnosisCorrectionDraft | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> Self:
        if self.action is DecisionAction.CORRECT:
            if self.correction is None:
                raise ValueError("correct action requires correction")
        elif self.correction is not None:
            raise ValueError("correction is only allowed for correct action")
        if self.action is DecisionAction.REJECT and not self.reason:
            raise ValueError("reject action requires reason")
        return self


class HumanReviewDecision(HumanDecisionDraft):
    decision_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    resulting_revision_id: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def validate_resulting_revision(self) -> Self:
        if self.action is DecisionAction.CORRECT:
            if self.resulting_revision_id is None:
                raise ValueError("correct decision requires a resulting revision")
        elif self.resulting_revision_id is not None:
            raise ValueError("only a correct decision has a resulting revision")
        return self


class DiagnosisReviewCase(ContractModel):
    case_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    status: ReviewStatus
    version: int = Field(ge=0)
    verification_mode: VerificationMode
    diagnoser: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)
    current_revision_number: int = Field(ge=0)
    evidence_revision_count: int = Field(ge=0, le=1)
    deterministic_run_id: str | None = None
    semantic_run_id: str | None = None
    composite_verdict: VerifierVerdict | None = None
    terminal_decision_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_case(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        terminal = self.status in {
            ReviewStatus.CONFIRMED,
            ReviewStatus.CORRECTED,
            ReviewStatus.REJECTED,
        }
        if terminal != (self.terminal_decision_id is not None):
            raise ValueError("terminal status and terminal_decision_id must agree")
        if self.verification_mode is VerificationMode.DETERMINISTIC and self.semantic_run_id:
            raise ValueError("deterministic mode forbids a semantic verifier run")
        return self


class WorkflowEvent(ContractModel):
    """Sanitized, append-only workflow event exposed by review aggregates."""

    event_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    event_sequence: int = Field(ge=0)
    event_type: WorkflowEventType
    from_status: ReviewStatus | None = None
    to_status: ReviewStatus
    case_version: int = Field(ge=0)
    created_at: datetime


def resume_requires_live_api(
    case: DiagnosisReviewCase,
    verifier_reports: tuple[VerifierReport, ...],
) -> bool:
    if case.status in {ReviewStatus.REVISION_REQUESTED, ReviewStatus.REVISING}:
        return case.diagnoser == DiagnoserKind.DEEPSEEK
    if case.status not in {
        ReviewStatus.PENDING_VERIFICATION,
        ReviewStatus.VERIFYING,
    }:
        return False

    current_reports = tuple(
        report
        for report in verifier_reports
        if report.revision_number == case.current_revision_number
    )
    deterministic = next(
        (
            report
            for report in current_reports
            if report.verifier_kind == VerifierKind.DETERMINISTIC
        ),
        None,
    )
    if deterministic is None:
        if case.verification_mode is VerificationMode.HYBRID:
            return True
        return (
            case.diagnoser == DiagnoserKind.DEEPSEEK
            and case.current_revision_number == 0
            and case.evidence_revision_count == 0
        )
    if deterministic.verdict is not VerifierVerdict.VERIFIED:
        return False
    if case.verification_mode is not VerificationMode.HYBRID:
        return False
    return not any(
        report.verifier_kind == VerifierKind.SEMANTIC
        for report in current_reports
    )


class DiagnosisReviewDetail(ContractRoot):
    schema_name: Literal["spanvouch.review"] = "spanvouch.review"
    schema_version: Literal["1.0"] = "1.0"
    case: DiagnosisReviewCase
    revisions: tuple[DiagnosisRevision, ...]
    verifier_reports: tuple[VerifierReport, ...] = ()
    events: tuple[WorkflowEvent, ...] = ()
    decision: HumanReviewDecision | None = None

    @model_validator(mode="before")
    @classmethod
    def discard_derived_resume_flag(cls, value: object) -> object:
        if isinstance(value, dict) and "resume_requires_live_api" in value:
            value = dict(value)
            value.pop("resume_requires_live_api")
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resume_requires_live_api(self) -> bool:
        return resume_requires_live_api(self.case, self.verifier_reports)
