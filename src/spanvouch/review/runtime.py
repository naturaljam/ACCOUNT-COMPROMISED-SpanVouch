from datetime import datetime
from typing import Self

from pydantic import model_validator

from spanvouch.contracts.review import DiagnosisReviewCase, DiagnosisRevision
from spanvouch.contracts.verification import ReviewInputSnapshot, VerifierReport
from spanvouch.contracts.versioning import ContractModel


class ReviewRuntimeBundle(ContractModel):
    """Private persisted workflow state; never part of the public contract."""

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
