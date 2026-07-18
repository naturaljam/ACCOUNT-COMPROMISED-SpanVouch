import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from spanvouch.contracts.diagnosis import ClaimStage, DiagnosisReport, ProviderUsage
from spanvouch.contracts.sanitization import sanitize_diagnostic_trace_view
from spanvouch.contracts.trace import DiagnosticTraceView
from spanvouch.contracts.versioning import (
    IDENTIFIER_PATTERN,
    SHA256_PATTERN,
    ContractModel,
    ContractRoot,
    canonical_json,
    canonical_sha256,
)


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


def _validate_sorted_unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{field_name} must be sorted and unique")
    return values


class VerificationFinding(ContractModel):
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


class EvidenceGap(ContractModel):
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


class VerifierProvenance(ContractModel):
    verifier_kind: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)
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


class OperationalErrorMetadata(ContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool


class ReviewInputSnapshot(ContractModel):
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


class VerificationInput(ContractModel):
    snapshot: ReviewInputSnapshot
    report: DiagnosisReport
    report_sha256: str = Field(pattern=SHA256_PATTERN)
    revision_number: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if self.report_sha256 != canonical_sha256(self.report):
            raise ValueError("report_sha256 does not match report")
        if self.report.trace_id != self.snapshot.trace_id:
            raise ValueError("report trace_id must match snapshot")
        if self.report.run_id != self.snapshot.run_id:
            raise ValueError("report run_id must match snapshot")
        return self


class VerifierReport(ContractRoot):
    schema_name: Literal["spanvouch.verification"] = "spanvouch.verification"
    schema_version: Literal["1.0"] = "1.0"
    verifier_run_id: str = Field(min_length=1)
    revision_number: int = Field(ge=0)
    report_sha256: str = Field(pattern=SHA256_PATTERN)
    verifier_kind: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)
    verdict: VerifierVerdict
    findings: tuple[VerificationFinding, ...] = ()
    evidence_gaps: tuple[EvidenceGap, ...] = ()
    alternative_failure_type: str | None = Field(default=None, min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    provenance: VerifierProvenance
    usage: ProviderUsage | None = None
    operational_error: OperationalErrorMetadata | None = None
    started_at: datetime
    completed_at: datetime

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if self.provenance.verifier_kind != self.verifier_kind:
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
        has_operational_finding = any(
            finding.code is FindingCode.PROVIDER_OPERATIONAL_ERROR
            for finding in self.findings
        )
        if has_operational_finding != (self.operational_error is not None):
            raise ValueError(
                "provider operational finding and operational_error must appear together"
            )
        if has_operational_finding and self.verdict is not VerifierVerdict.REVIEW_REQUIRED:
            raise ValueError("operational state requires review_required verdict")
        return self
