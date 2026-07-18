from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from afc.failure_types import SUPPORTED_DIAGNOSIS_FAILURE_TYPES, FailureType


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


class EvidenceSelector(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    span_id: str = Field(min_length=1)
    field_path: str = Field(min_length=1)

    @property
    def canonical(self) -> str:
        return f"{self.span_id}::{self.field_path}"


class EvidenceRef(EvidenceSelector):
    evidence_id: str = Field(min_length=1)
    observed_value: JsonValue
    value_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    description: str = Field(min_length=1)


class DiagnosisClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: ClaimStage
    statement: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class DiagnosisDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DiagnosisStatus
    failure_type: FailureType | None = None
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
            if self.failure_type not in SUPPORTED_DIAGNOSIS_FAILURE_TYPES:
                raise ValueError("diagnosed status requires a supported failure type")
            if not self.critical_span_ids or not self.causal_chain or not self.evidence:
                raise ValueError("diagnosed status requires critical spans, claims, and evidence")
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


class DiagnosisProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    taxonomy_version: str = Field(min_length=1)
    diagnoser_version: str = Field(min_length=1)
    ruleset_version: str | None = None
    prompt_version: str | None = None
    prompt_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model: str | None = None
    provider: str | None = None


class ProviderUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0.0)
    request_id: str | None = None


class DiagnosisExecution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: DiagnosisDecision
    provenance: DiagnosisProvenance
    usage: ProviderUsage | None = None


class DiagnosisReport(DiagnosisDecision):
    schema_version: Literal["1.0"] = "1.0"
    trace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    diagnoser: DiagnoserKind
    provenance: DiagnosisProvenance
    usage: ProviderUsage | None = None
