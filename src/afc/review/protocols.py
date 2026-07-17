from typing import Protocol

from afc.diagnosis.models import DiagnoserKind, DiagnosisReport
from afc.review.commands import (
    AppendDiagnosisRevision,
    AppendVerifierRun,
    ApplyHumanDecision,
    ClaimReviewWork,
    CreateReviewCase,
    RouteRevisionFailureToHuman,
    RouteToHumanReview,
)
from afc.review.models import (
    DiagnosisReviewCase,
    DiagnosisReviewDetail,
    EvidenceGap,
    ReviewRuntimeBundle,
    VerificationInput,
    VerifierKind,
    VerifierReport,
)


class Verifier(Protocol):
    kind: VerifierKind
    version_fingerprint: str

    async def verify(self, input_: VerificationInput) -> VerifierReport:
        raise NotImplementedError


class ReviewWorkflowRunner(Protocol):
    async def run(self, case_id: str) -> DiagnosisReviewDetail:
        raise NotImplementedError

    async def resume(self, case_id: str) -> DiagnosisReviewDetail:
        raise NotImplementedError


class ReviewReviser(Protocol):
    def supports(self, diagnoser_kind: DiagnoserKind) -> bool:
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

    async def append_verifier_run(self, command: AppendVerifierRun) -> DiagnosisReviewCase:
        raise NotImplementedError

    async def append_revision(self, command: AppendDiagnosisRevision) -> DiagnosisReviewCase:
        raise NotImplementedError

    async def route_to_human(self, command: RouteToHumanReview) -> DiagnosisReviewCase:
        raise NotImplementedError

    async def route_revision_failure(
        self, command: RouteRevisionFailureToHuman
    ) -> DiagnosisReviewCase:
        raise NotImplementedError

    async def apply_human_decision(
        self, command: ApplyHumanDecision
    ) -> DiagnosisReviewDetail:
        raise NotImplementedError
