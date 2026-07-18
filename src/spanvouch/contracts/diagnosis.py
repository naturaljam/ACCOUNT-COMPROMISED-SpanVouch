from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from spanvouch.contracts.versioning import (
    IDENTIFIER_PATTERN,
    SHA256_PATTERN,
    ContractModel,
    ContractRoot,
)


class DiagnosisStatus(StrEnum):
    DIAGNOSED = "diagnosed"
    NO_FAILURE = "no_failure"
    ABSTAINED = "abstained"


class DiagnoserKind(StrEnum):
    RULES = "rules"
    DEEPSEEK = "deepseek"


class AbstainReason(StrEnum):
    UNSUPPORTED_FAILURE_TYPE = "unsupported_failure_type"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    AMBIGUOUS_FINDINGS = "ambiguous_findings"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    INVALID_EVIDENCE_REFERENCE = "invalid_evidence_reference"


class ClaimStage(StrEnum):
    CAUSE = "cause"
    PROPAGATION = "propagation"
    OUTCOME = "outcome"


class EvidenceSelector(ContractModel):
    span_id: str = Field(min_length=1)
    field_path: str = Field(min_length=1)

    @property
    def canonical(self) -> str:
        return f"{self.span_id}::{self.field_path}"


class EvidenceRef(EvidenceSelector):
    evidence_id: str = Field(min_length=1)
    observed_value: JsonValue
    value_sha256: str = Field(pattern=SHA256_PATTERN)
    description: str = Field(min_length=1)


class DiagnosisClaim(ContractModel):
    stage: ClaimStage
    statement: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class TaxonomyRef(ContractModel):
    taxonomy_id: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)
    taxonomy_version: str = Field(min_length=1)


class DiagnosisProvenance(ContractModel):
    taxonomy: TaxonomyRef
    diagnoser_version: str = Field(min_length=1)
    ruleset_version: str | None = None
    prompt_version: str | None = None
    prompt_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    model: str | None = None
    provider: str | None = None


class ProviderUsage(ContractModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0.0)
    request_id: str | None = None


class DiagnosisDecision(ContractModel):
    status: DiagnosisStatus
    failure_type: str | None = Field(default=None, min_length=1)
    critical_span_ids: tuple[str, ...] = ()
    causal_chain: tuple[DiagnosisClaim, ...] = Field(default=(), max_length=3)
    evidence: tuple[EvidenceRef, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    abstain_reason: AbstainReason | None = None

    @model_validator(mode="after")
    def validate_decision_state(self) -> Self:
        if len(self.critical_span_ids) != len(set(self.critical_span_ids)):
            raise ValueError("critical_span_ids must be unique")

        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id must be unique")
        known_evidence = set(evidence_ids)
        if any(
            evidence_id not in known_evidence
            for claim in self.causal_chain
            for evidence_id in claim.evidence_ids
        ):
            raise ValueError("claim references unknown evidence")

        if self.status is DiagnosisStatus.DIAGNOSED:
            if self.failure_type is None:
                raise ValueError("diagnosed status requires a failure type")
            if not self.critical_span_ids or not self.causal_chain or not self.evidence:
                raise ValueError("diagnosed status requires critical spans, claims, and evidence")
            if self.abstain_reason is not None:
                raise ValueError("diagnosed status forbids abstain_reason")
        elif self.status is DiagnosisStatus.NO_FAILURE:
            if self.failure_type != "no_failure":
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


class DiagnosisExecution(ContractModel):
    decision: DiagnosisDecision
    provenance: DiagnosisProvenance
    usage: ProviderUsage | None = None


class DiagnosisReport(DiagnosisDecision, ContractRoot):
    schema_name: Literal["spanvouch.diagnosis"] = "spanvouch.diagnosis"
    schema_version: Literal["1.0"] = "1.0"
    trace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    diagnoser: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)
    provenance: DiagnosisProvenance
    usage: ProviderUsage | None = None

    @classmethod
    def from_execution(
        cls,
        *,
        trace_id: str,
        run_id: str,
        diagnoser: str,
        execution: DiagnosisExecution,
    ) -> "DiagnosisReport":
        return cls(
            trace_id=trace_id,
            run_id=run_id,
            diagnoser=diagnoser,
            provenance=execution.provenance,
            usage=execution.usage,
            **execution.decision.model_dump(mode="python"),
        )
