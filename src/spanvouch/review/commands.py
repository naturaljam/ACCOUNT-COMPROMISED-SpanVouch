from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from spanvouch.contracts.diagnosis import DiagnoserKind
from spanvouch.contracts.verification import (
    ReviewInputSnapshot,
    VerificationMode,
    VerifierKind,
    VerifierReport,
    VerifierVerdict,
)
from spanvouch.review.models import (
    DecisionAction,
    DiagnosisRevision,
    HumanReviewDecision,
    ReviewStatus,
    RevisionOrigin,
    canonical_json,
)
from spanvouch.review.models import WorkflowEventType as WorkflowEventType

SHA256_PATTERN = r"^[0-9a-f]{64}$"


def human_decision_transition(
    action: DecisionAction,
) -> tuple[ReviewStatus, WorkflowEventType]:
    return {
        DecisionAction.CONFIRM: (
            ReviewStatus.CONFIRMED,
            WorkflowEventType.HUMAN_CONFIRMED,
        ),
        DecisionAction.CORRECT: (
            ReviewStatus.CORRECTED,
            WorkflowEventType.HUMAN_CORRECTED,
        ),
        DecisionAction.REJECT: (
            ReviewStatus.REJECTED,
            WorkflowEventType.HUMAN_REJECTED,
        ),
    }[action]


def _require_utc(value: datetime, field_name: str) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError(f"{field_name} must be an aware UTC timestamp")


class ReviewCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def validate_datetime_fields(cls, value: Any, info: Any) -> Any:
        if isinstance(value, datetime):
            _require_utc(value, info.field_name)
        return value


class EventCommand(ReviewCommand):
    event_id: str = Field(min_length=1)
    event_type: WorkflowEventType
    event_metadata_json: str = Field(min_length=2)

    @field_validator("event_metadata_json")
    @classmethod
    def validate_event_metadata(cls, value: str) -> str:
        if canonical_json(value) != value:
            raise ValueError("event_metadata_json must use canonical JSON")
        return value


class TransitionCommand(EventCommand):
    case_id: str = Field(min_length=1)
    expected_version: int = Field(ge=0)
    prior_status: ReviewStatus
    target_status: ReviewStatus
    occurred_at: datetime

    def require_valid_transition(self) -> None:
        raise NotImplementedError


class CreateReviewCase(EventCommand):
    case_id: str = Field(min_length=1)
    snapshot: ReviewInputSnapshot
    initial_revision: DiagnosisRevision
    target_status: ReviewStatus
    verification_mode: VerificationMode
    diagnoser: DiagnoserKind
    idempotency_scope: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    idempotency_reservation_id: str | None = Field(default=None, min_length=1)
    created_at: datetime

    @model_validator(mode="after")
    def validate_create_binding(self) -> Self:
        if self.target_status is not ReviewStatus.PENDING_VERIFICATION:
            raise ValueError("new review cases must be pending verification")
        if self.event_type is not WorkflowEventType.CASE_CREATED:
            raise ValueError("new review cases require a case_created event")
        if self.initial_revision.case_id != self.case_id:
            raise ValueError("initial revision case_id must match command")
        if self.initial_revision.revision_number != 0:
            raise ValueError("initial revision must be revision zero")
        if self.initial_revision.report.trace_id != self.snapshot.trace_id:
            raise ValueError("initial report trace_id must match snapshot")
        if self.initial_revision.report.run_id != self.snapshot.run_id:
            raise ValueError("initial report run_id must match snapshot")
        if self.initial_revision.report.diagnoser != self.diagnoser:
            raise ValueError("initial report diagnoser must match command")
        for field_name, timestamp in (
            ("snapshot.created_at", self.snapshot.created_at),
            ("initial_revision.created_at", self.initial_revision.created_at),
        ):
            _require_utc(timestamp, field_name)
        return self


class ClaimReviewWork(TransitionCommand):
    lease_owner: str = Field(min_length=1)
    lease_expires_at: datetime
    now: datetime

    def require_valid_transition(self) -> None:
        allowed = {
            (
                ReviewStatus.PENDING_VERIFICATION,
                ReviewStatus.VERIFYING,
                WorkflowEventType.VERIFICATION_STARTED,
            ),
            (
                ReviewStatus.VERIFYING,
                ReviewStatus.VERIFYING,
                WorkflowEventType.VERIFICATION_STARTED,
            ),
            (
                ReviewStatus.REVISION_REQUESTED,
                ReviewStatus.REVISING,
                WorkflowEventType.REVISION_STARTED,
            ),
            (
                ReviewStatus.REVISING,
                ReviewStatus.REVISING,
                WorkflowEventType.REVISION_STARTED,
            ),
        }
        if (self.prior_status, self.target_status, self.event_type) not in allowed:
            raise ValueError("invalid claim transition")

    @model_validator(mode="after")
    def validate_lease_window(self) -> Self:
        self.require_valid_transition()
        if self.lease_expires_at <= self.now:
            raise ValueError("lease_expires_at must be later than now")
        return self


class ReviewLeaseWork(StrEnum):
    SEMANTIC_VERIFICATION = "semantic_verification"
    EVIDENCE_REVISION = "evidence_revision"


class RenewReviewLease(ReviewCommand):
    case_id: str = Field(min_length=1)
    expected_version: int = Field(ge=0)
    expected_status: ReviewStatus
    lease_owner: str = Field(min_length=1)
    work: ReviewLeaseWork
    now: datetime
    lease_expires_at: datetime

    @model_validator(mode="after")
    def validate_renewal(self) -> Self:
        expected_status = {
            ReviewLeaseWork.SEMANTIC_VERIFICATION: ReviewStatus.VERIFYING,
            ReviewLeaseWork.EVIDENCE_REVISION: ReviewStatus.REVISING,
        }[self.work]
        if self.expected_status is not expected_status:
            raise ValueError("review lease work does not match active status")
        if self.lease_expires_at <= self.now:
            raise ValueError("lease_expires_at must be later than now")
        return self


class AppendVerifierRun(TransitionCommand):
    report: VerifierReport
    composite_verdict: VerifierVerdict
    lease_owner: str | None = Field(default=None, min_length=1)

    def require_valid_transition(self) -> None:
        allowed = {
            (
                ReviewStatus.VERIFYING,
                ReviewStatus.VERIFYING,
                WorkflowEventType.VERIFICATION_COMPLETED,
            ),
            (
                ReviewStatus.VERIFYING,
                ReviewStatus.REVISION_REQUESTED,
                WorkflowEventType.REVISION_REQUESTED,
            ),
            (
                ReviewStatus.VERIFYING,
                ReviewStatus.VERIFYING,
                WorkflowEventType.PROVIDER_FAILED,
            ),
        }
        if (self.prior_status, self.target_status, self.event_type) not in allowed:
            raise ValueError("invalid verifier transition")

    @model_validator(mode="after")
    def validate_report_event(self) -> Self:
        self.require_valid_transition()
        if (
            self.report.verifier_kind == VerifierKind.SEMANTIC
            and self.lease_owner is None
        ):
            raise ValueError("semantic verifier result requires lease_owner")
        _require_utc(self.report.started_at, "report.started_at")
        _require_utc(self.report.completed_at, "report.completed_at")
        return self


class AppendDiagnosisRevision(TransitionCommand):
    revision: DiagnosisRevision
    lease_owner: str = Field(min_length=1)

    def require_valid_transition(self) -> None:
        expected = (
            ReviewStatus.REVISING,
            ReviewStatus.VERIFYING,
            WorkflowEventType.REVISION_COMPLETED,
        )
        if (self.prior_status, self.target_status, self.event_type) != expected:
            raise ValueError("invalid revision transition")

    @model_validator(mode="after")
    def validate_revision_binding(self) -> Self:
        self.require_valid_transition()
        if self.revision.case_id != self.case_id:
            raise ValueError("revision case_id must match command")
        if self.revision.origin is not RevisionOrigin.EVIDENCE_REVISION:
            raise ValueError("append_revision accepts only an evidence revision")
        _require_utc(self.revision.created_at, "revision.created_at")
        return self


class RouteToHumanReview(TransitionCommand):
    def require_valid_transition(self) -> None:
        expected = (
            ReviewStatus.VERIFYING,
            ReviewStatus.AWAITING_HUMAN_REVIEW,
            WorkflowEventType.AWAITING_HUMAN_REVIEW,
        )
        if (self.prior_status, self.target_status, self.event_type) != expected:
            raise ValueError("invalid human-route transition")

    @model_validator(mode="after")
    def validate_route(self) -> Self:
        self.require_valid_transition()
        return self


class FinalizeSemanticFailure(ReviewCommand):
    verifier: AppendVerifierRun
    route: RouteToHumanReview

    @model_validator(mode="after")
    def validate_atomic_transition(self) -> Self:
        verifier = self.verifier
        route = self.route
        if verifier.report.verifier_kind != VerifierKind.SEMANTIC:
            raise ValueError("semantic failure finalization requires a semantic report")
        if verifier.report.operational_error is None:
            raise ValueError("semantic failure finalization requires an operational error")
        if verifier.event_type is not WorkflowEventType.PROVIDER_FAILED:
            raise ValueError("semantic failure finalization requires provider_failed")
        if verifier.target_status is not ReviewStatus.VERIFYING:
            raise ValueError("semantic failure verifier transition must remain verifying")
        if (
            route.case_id != verifier.case_id
            or route.expected_version != verifier.expected_version + 1
            or route.prior_status is not verifier.target_status
            or route.occurred_at != verifier.occurred_at
        ):
            raise ValueError("semantic failure route must follow the verifier transition")
        return self


class RouteRevisionFailureToHuman(TransitionCommand):
    composite_verdict: VerifierVerdict
    lease_owner: str = Field(min_length=1)

    def require_valid_transition(self) -> None:
        expected = (
            ReviewStatus.REVISING,
            ReviewStatus.AWAITING_HUMAN_REVIEW,
            WorkflowEventType.REVISION_PROVIDER_FAILED,
        )
        if (self.prior_status, self.target_status, self.event_type) != expected:
            raise ValueError("invalid revision-failure transition")

    @model_validator(mode="after")
    def validate_route(self) -> Self:
        self.require_valid_transition()
        if self.composite_verdict is not VerifierVerdict.REVIEW_REQUIRED:
            raise ValueError("revision failure must require human review")
        return self


class ApplyHumanDecision(TransitionCommand):
    decision: HumanReviewDecision
    correction_revision: DiagnosisRevision | None
    correction_verifier_report: VerifierReport | None = None
    idempotency_scope: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    request_sha256: str = Field(pattern=SHA256_PATTERN)

    def require_valid_transition(self) -> None:
        target, event = human_decision_transition(self.decision.action)
        expected = (ReviewStatus.AWAITING_HUMAN_REVIEW, target, event)
        if (self.prior_status, self.target_status, self.event_type) != expected:
            raise ValueError(
                "invalid human-decision transition: requires awaiting_human_review"
            )

    @model_validator(mode="after")
    def validate_decision_binding(self) -> Self:
        self.require_valid_transition()
        if self.decision.case_id != self.case_id:
            raise ValueError("decision case_id must match command")
        if self.decision.expected_version != self.expected_version:
            raise ValueError("decision expected_version must match command")
        if self.decision.action is DecisionAction.CORRECT:
            if self.correction_revision is None:
                raise ValueError("correct decisions require a correction revision")
            if self.correction_verifier_report is None:
                raise ValueError("correct decisions require a correction verifier report")
            if self.correction_revision.case_id != self.case_id:
                raise ValueError("correction revision case_id must match command")
            if self.correction_revision.origin is not RevisionOrigin.HUMAN_CORRECTION:
                raise ValueError("correction revision must have human correction origin")
            if self.correction_revision.revision_id != self.decision.resulting_revision_id:
                raise ValueError("decision resulting revision must match correction revision")
            verifier_report = self.correction_verifier_report
            if verifier_report.verifier_kind != VerifierKind.DETERMINISTIC:
                raise ValueError("correction verifier report must be deterministic")
            if verifier_report.verdict is not VerifierVerdict.VERIFIED:
                raise ValueError("correction verifier report must be verified")
            if verifier_report.operational_error is not None:
                raise ValueError("correction verifier report cannot be operational")
            if verifier_report.revision_number != self.correction_revision.revision_number:
                raise ValueError("correction verifier revision must match correction revision")
            if verifier_report.report_sha256 != self.correction_revision.report_sha256:
                raise ValueError("correction verifier report hash must match correction revision")
            _require_utc(verifier_report.started_at, "correction_verifier_report.started_at")
            _require_utc(verifier_report.completed_at, "correction_verifier_report.completed_at")
            _require_utc(self.correction_revision.created_at, "correction_revision.created_at")
        elif self.correction_revision is not None or self.correction_verifier_report is not None:
            raise ValueError("only correct decisions may include correction records")
        _require_utc(self.decision.created_at, "decision.created_at")
        return self
