from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from spanvouch.contracts.trace import DiagnosticTraceView
from spanvouch.diagnosis.models import EvidenceRef
from spanvouch.failure_types import FailureType
from spanvouch.trace.evidence_catalog import EvidenceCatalog


class InvariantStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class RuleScope(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED_GUARD = "unsupported_guard"


class InvariantResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str = Field(min_length=1)
    rule_version: str = Field(min_length=1)
    status: InvariantStatus
    severity: Severity
    failure_type: FailureType | None = None
    scope: RuleScope
    evidence: tuple[EvidenceRef, ...] = ()
    explanation: str = Field(min_length=1)
    hard_failure: bool = False


@dataclass(frozen=True)
class RuleContext:
    view: DiagnosticTraceView
    evidence: EvidenceCatalog


class InvariantRule(Protocol):
    rule_id: str
    rule_version: str

    def evaluate(self, context: RuleContext) -> InvariantResult:
        raise NotImplementedError
