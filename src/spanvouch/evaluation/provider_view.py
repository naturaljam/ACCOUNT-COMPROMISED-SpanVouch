"""Provider-safe verification inputs.

This module deliberately depends on review candidates but never on evaluator labels.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from spanvouch.contracts.diagnosis import DiagnosisReport
from spanvouch.contracts.trace import TraceIR
from spanvouch.contracts.verification import ReviewInputSnapshot, VerificationInput
from spanvouch.contracts.versioning import canonical_json, canonical_sha256
from spanvouch.evaluation.generate_review_dataset import ReviewCandidate
from spanvouch.trace.diagnostic_view import TraceProjector


class ProviderVisibleVerificationInput(BaseModel):
    """The only evaluator object that may cross into a provider-backed verifier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    diagnosis: DiagnosisReport
    diagnosis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verifier_contract: str = Field(min_length=1)

    def bind_trace(self, trace: TraceIR) -> VerificationInput:
        view = TraceProjector().project(trace).view
        snapshot = ReviewInputSnapshot(
            trace_id=trace.trace_id,
            run_id=trace.run_id,
            view_json=canonical_json(view),
            input_sha256=canonical_sha256(view),
            catalog_version="evidence-catalog-v1",
            created_at=_SNAPSHOT_TIME,
        )
        return VerificationInput(
            snapshot=snapshot,
            report=self.diagnosis,
            report_sha256=self.diagnosis_sha256,
        )


def build_verifier_provider_view(candidate: ReviewCandidate) -> ProviderVisibleVerificationInput:
    """Extract the provider-safe diagnosis, excluding cohort and scoring metadata."""
    return ProviderVisibleVerificationInput(
        diagnosis=candidate.report,
        diagnosis_sha256=canonical_sha256(candidate.report),
        verifier_contract="spanvouch.verification/1.0",
    )


_SNAPSHOT_TIME = datetime(2026, 7, 17, tzinfo=UTC)
