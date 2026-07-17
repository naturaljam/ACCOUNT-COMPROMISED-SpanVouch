from typing import Protocol

from afc.review.commands import (
    AppendDiagnosisRevision,
    AppendVerifierRun,
    ApplyHumanDecision,
    ClaimReviewWork,
    CreateReviewCase,
    RouteToHumanReview,
)
from afc.review.models import (
    DiagnosisReviewCase,
    DiagnosisReviewDetail,
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


class ReviewRepository(Protocol):
    async def initialize(self) -> None:
        raise NotImplementedError

    async def create_case(self, command: CreateReviewCase) -> DiagnosisReviewDetail:
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

    async def apply_human_decision(
        self, command: ApplyHumanDecision
    ) -> DiagnosisReviewDetail:
        raise NotImplementedError
