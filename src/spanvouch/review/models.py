import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from spanvouch.contracts.trace import DiagnosticTraceView
from spanvouch.contracts.versioning import (
    canonical_json as canonical_json,
)
from spanvouch.contracts.versioning import (
    canonical_sha256 as canonical_sha256,
)
from spanvouch.diagnosis.models import (
    AbstainReason,
    ClaimStage,
    DiagnoserKind,
    DiagnosisProvenance,
    DiagnosisReport,
    DiagnosisStatus,
    EvidenceSelector,
    ProviderUsage,
)
from spanvouch.failure_types import SUPPORTED_DIAGNOSIS_FAILURE_TYPES, FailureType
from spanvouch.trace.diagnostic_view import sanitize_diagnostic_trace_view

SHA256_PATTERN = r"^[0-9a-f]{64}$"


class ReviewStatus(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    VERIFYING = "verifying"
    REVISION_REQUESTED = "revision_requested"
    REVISING = "revising"
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    REJECTED = "rejected"


class VerificationMode(StrEnum):
    DETERMINISTIC = "deterministic"
    HYBRID = "hybrid"


class VerifierKind(StrEnum):
    DETERMINISTIC = "deterministic"
    SEMANTIC = "semantic"


class VerifierVerdict(StrEnum):
    VERIFIED = "verified"
    NEEDS_EVIDENCE = "needs_evidence"
    REVIEW_REQUIRED = "review_required"


class FindingSeverity(StrEnum):
    HARD = "hard"
    ADVISORY = "advisory"
    OPERATIONAL = "operational"


class FindingCode(StrEnum):
    INVALID_SELECTOR = "invalid_selector"
    EVIDENCE_VALUE_MISMATCH = "evidence_value_mismatch"
    EVIDENCE_HASH_MISMATCH = "evidence_hash_mismatch"
    CLAIM_NOT_GROUNDED = "claim_not_grounded"
    CRITICAL_SPAN_NOT_GROUNDED = "critical_span_not_grounded"
    DUPLICATE_REFERENCE = "duplicate_reference"
    EVIDENCE_BUDGET_EXCEEDED = "evidence_budget_exceeded"
    CLEAN_TRACE_CONFLICT = "clean_trace_conflict"
    UNSUPPORTED_SCOPE = "unsupported_scope"
    DIAGNOSIS_CONFLICT = "diagnosis_conflict"
    ALTERNATIVE_HYPOTHESIS = "alternative_hypothesis"
    SEMANTIC_SUPPORT_MISSING = "semantic_support_missing"
    INVALID_VERIFIER_OUTPUT = "invalid_verifier_output"
    PROVIDER_OPERATIONAL_ERROR = "provider_operational_error"


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


class ReviewModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _validate_sorted_unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{field_name} must be sorted and unique")
    return values


class VerificationFinding(ReviewModel):
    finding_id: str = Field(min_length=1)
    code: FindingCode
    severity: FindingSeverity
    message: str = Field(min_length=1, max_length=500)
    revisable: bool
    related_selectors: tuple[str, ...] = ()
    related_span_ids: tuple[str, ...] = ()

    @field_validator("related_selectors", "related_span_ids")
    @classmethod
    def validate_sorted_references(cls, values: tuple[str, ...], info: Any) -> tuple[str, ...]:
        return _validate_sorted_unique(values, info.field_name)


class EvidenceGap(ReviewModel):
    gap_id: str = Field(min_length=1)
    finding_code: FindingCode
    claim_index: int | None = Field(default=None, ge=0)
    stage: ClaimStage | None = None
    required_evidence_kind: str = Field(min_length=1, max_length=100)
    allowed_selectors: tuple[str, ...] = ()
    related_span_ids: tuple[str, ...] = ()
    instruction: str = Field(min_length=1, max_length=500)

    @field_validator("allowed_selectors", "related_span_ids")
    @classmethod
    def validate_sorted_references(cls, values: tuple[str, ...], info: Any) -> tuple[str, ...]:
        return _validate_sorted_unique(values, info.field_name)


class VerifierProvenance(ReviewModel):
    verifier_kind: VerifierKind
    verifier_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    prompt_version: str | None = None
    prompt_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    model: str | None = None
    provider: str | None = None

    @model_validator(mode="after")
    def validate_prompt_metadata(self) -> Self:
        if (self.prompt_version is None) != (self.prompt_sha256 is None):
            raise ValueError("prompt_version and prompt_sha256 must be provided together")
        return self


class OperationalErrorMetadata(ReviewModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool


class VerifierReport(ReviewModel):
    verifier_run_id: str = Field(min_length=1)
    revision_number: int = Field(ge=0)
    report_sha256: str = Field(pattern=SHA256_PATTERN)
    verifier_kind: VerifierKind
    verdict: VerifierVerdict
    findings: tuple[VerificationFinding, ...] = ()
    evidence_gaps: tuple[EvidenceGap, ...] = ()
    alternative_failure_type: FailureType | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    provenance: VerifierProvenance
    usage: ProviderUsage | None = None
    operational_error: OperationalErrorMetadata | None = None
    started_at: datetime
    completed_at: datetime

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if self.provenance.verifier_kind is not self.verifier_kind:
            raise ValueError("provenance verifier_kind must match report verifier_kind")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        finding_ids = [finding.finding_id for finding in self.findings]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("finding_id must be unique")
        gap_ids = [gap.gap_id for gap in self.evidence_gaps]
        if len(gap_ids) != len(set(gap_ids)):
            raise ValueError("gap_id must be unique")
        if self.verdict is VerifierVerdict.VERIFIED:
            if any(finding.severity is FindingSeverity.HARD for finding in self.findings):
                raise ValueError("verified verdict forbids hard findings")
            if self.evidence_gaps:
                raise ValueError("verified verdict forbids evidence gaps")
        if self.operational_error is not None and not any(
            finding.code is FindingCode.PROVIDER_OPERATIONAL_ERROR
            for finding in self.findings
        ):
            raise ValueError("operational_error requires a provider operational finding")
        return self


class DiagnosisRevision(ReviewModel):
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


class ReviewInputSnapshot(ReviewModel):
    trace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    view_json: str = Field(min_length=1)
    input_sha256: str = Field(pattern=SHA256_PATTERN)
    catalog_version: str = Field(min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        try:
            parsed = json.loads(self.view_json)
        except json.JSONDecodeError as error:
            raise ValueError("view_json must be valid JSON") from error
        if self.view_json != canonical_json(parsed):
            raise ValueError("view_json must use canonical JSON")
        view = sanitize_diagnostic_trace_view(DiagnosticTraceView.model_validate(parsed))
        sanitized_view_json = canonical_json(view)
        if self.view_json != sanitized_view_json:
            raise ValueError(
                "view_json must match the sanitized canonical diagnostic trace view"
            )
        if self.input_sha256 != canonical_sha256(view):
            raise ValueError("input_sha256 does not match sanitized view_json")
        if not view.spans:
            raise ValueError("view_json must contain a diagnostic trace view")
        return self

    def trace_view(self) -> DiagnosticTraceView:
        return DiagnosticTraceView.model_validate_json(self.view_json)


class VerificationInput(ReviewModel):
    snapshot: ReviewInputSnapshot
    report: DiagnosisReport
    report_sha256: str = Field(pattern=SHA256_PATTERN)
    revision_number: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if self.report.trace_id != self.snapshot.trace_id:
            raise ValueError("report trace_id must match snapshot")
        if self.report.run_id != self.snapshot.run_id:
            raise ValueError("report run_id must match snapshot")
        return self


class CorrectionClaim(ReviewModel):
    stage: ClaimStage
    statement: str = Field(min_length=1)
    selectors: tuple[EvidenceSelector, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_selectors(self) -> Self:
        canonical = tuple(selector.canonical for selector in self.selectors)
        if canonical != tuple(sorted(set(canonical))):
            raise ValueError("claim selectors must be sorted and unique")
        return self


class DiagnosisCorrectionDraft(ReviewModel):
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


class HumanDecisionDraft(ReviewModel):
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


class DiagnosisReviewCase(ReviewModel):
    case_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    status: ReviewStatus
    version: int = Field(ge=0)
    verification_mode: VerificationMode
    diagnoser: DiagnoserKind
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


class WorkflowEvent(ReviewModel):
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
        return case.diagnoser is DiagnoserKind.DEEPSEEK
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
            if report.verifier_kind is VerifierKind.DETERMINISTIC
        ),
        None,
    )
    if deterministic is None:
        if case.verification_mode is VerificationMode.HYBRID:
            return True
        return (
            case.diagnoser is DiagnoserKind.DEEPSEEK
            and case.current_revision_number == 0
            and case.evidence_revision_count == 0
        )
    if deterministic.verdict is not VerifierVerdict.VERIFIED:
        return False
    if case.verification_mode is not VerificationMode.HYBRID:
        return False
    return not any(
        report.verifier_kind is VerifierKind.SEMANTIC
        for report in current_reports
    )


class DiagnosisReviewDetail(ReviewModel):
    case: DiagnosisReviewCase
    revisions: tuple[DiagnosisRevision, ...]
    verifier_reports: tuple[VerifierReport, ...] = ()
    events: tuple[WorkflowEvent, ...] = ()
    decision: HumanReviewDecision | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resume_requires_live_api(self) -> bool:
        return resume_requires_live_api(self.case, self.verifier_reports)


class ReviewRuntimeBundle(ReviewModel):
    case: DiagnosisReviewCase
    snapshot: ReviewInputSnapshot
    revisions: tuple[DiagnosisRevision, ...]
    verifier_reports: tuple[VerifierReport, ...] = ()
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_snapshot_binding(self) -> Self:
        if self.case.trace_id != self.snapshot.trace_id or self.case.run_id != self.snapshot.run_id:
            raise ValueError("case trace/run binding must match snapshot")
        if any(revision.case_id != self.case.case_id for revision in self.revisions):
            raise ValueError("revision case_id must match case")
        if (self.lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("runtime lease owner and expiry must agree")
        return self
