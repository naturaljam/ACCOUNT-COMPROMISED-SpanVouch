from datetime import datetime
from typing import Protocol

from spanvouch.contracts.diagnosis import DiagnosisReport
from spanvouch.contracts.review import (
    DiagnosisReviewCase,
    DiagnosisReviewDetail,
)
from spanvouch.contracts.trace import TraceIR
from spanvouch.contracts.verification import (
    EvidenceGap,
)
from spanvouch.review.commands import (
    AppendDiagnosisRevision,
    AppendVerifierRun,
    ApplyHumanDecision,
    ClaimReviewWork,
    CreateReviewCase,
    FinalizeSemanticFailure,
    RenewReviewLease,
    RouteCappedRevisionToHuman,
    RouteRevisionFailureToHuman,
    RouteToHumanReview,
)
from spanvouch.review.runtime import ReviewRuntimeBundle


class DiagnosisRunner(Protocol):
    async def diagnose(
        self,
        trace: TraceIR,
        kind: str,
        *,
        idempotency_key: str | None = None,
    ) -> DiagnosisReport:
        raise NotImplementedError


class ReviewWorkflowRunner(Protocol):
    async def run(self, case_id: str) -> DiagnosisReviewDetail:
        raise NotImplementedError

    async def resume(self, case_id: str) -> DiagnosisReviewDetail:
        raise NotImplementedError


class ReviewReviser(Protocol):
    def supports(self, diagnoser_kind: str) -> bool:
        raise NotImplementedError

    async def revise(
        self,
        runtime_bundle: ReviewRuntimeBundle,
        evidence_gaps: tuple[EvidenceGap, ...],
    ) -> DiagnosisReport:
        raise NotImplementedError


class ReviewRepository(Protocol):
    async def initialize(self) -> None:
        raise NotImplementedError

    async def create_case(self, command: CreateReviewCase) -> DiagnosisReviewDetail:
        raise NotImplementedError

    async def reserve_create(
        self,
        scope: str,
        idempotency_key: str,
        request_sha256: str,
        *,
        reservation_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> DiagnosisReviewDetail | None:
        raise NotImplementedError

    async def renew_create_reservation(
        self,
        scope: str,
        idempotency_key: str,
        request_sha256: str,
        *,
        reservation_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> None:
        raise NotImplementedError

    async def replay_detail(
        self,
        scope: str,
        idempotency_key: str,
        request_sha256: str,
        *,
        result_type: str,
    ) -> DiagnosisReviewDetail | None:
        raise NotImplementedError

    async def get_detail(self, case_id: str) -> DiagnosisReviewDetail:
        raise NotImplementedError

    async def load_runtime(self, case_id: str) -> ReviewRuntimeBundle:
        raise NotImplementedError

    async def claim_work(self, command: ClaimReviewWork) -> DiagnosisReviewCase:
        raise NotImplementedError

    async def renew_review_lease(self, command: RenewReviewLease) -> None:
        raise NotImplementedError

    async def append_verifier_run(self, command: AppendVerifierRun) -> DiagnosisReviewCase:
        raise NotImplementedError

    async def append_revision(self, command: AppendDiagnosisRevision) -> DiagnosisReviewCase:
        raise NotImplementedError

    async def finalize_semantic_failure(
        self, command: FinalizeSemanticFailure
    ) -> DiagnosisReviewCase:
        raise NotImplementedError

    async def route_to_human(self, command: RouteToHumanReview) -> DiagnosisReviewCase:
        raise NotImplementedError

    async def route_capped_revision_to_human(
        self, command: RouteCappedRevisionToHuman
    ) -> DiagnosisReviewCase:
        raise NotImplementedError

    async def route_revision_failure(
        self, command: RouteRevisionFailureToHuman
    ) -> DiagnosisReviewCase:
        raise NotImplementedError

    async def apply_human_decision(
        self, command: ApplyHumanDecision
    ) -> DiagnosisReviewDetail:
        raise NotImplementedError
