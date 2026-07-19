from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import JsonValue

from spanvouch.contracts.diagnosis import EvidenceRef, EvidenceSelector
from spanvouch.contracts.trace import DiagnosticSpan
from spanvouch.failure_types import FailureType
from spanvouch.verification.invariants import (
    InvariantResult,
    InvariantRule,
    InvariantStatus,
    RuleContext,
    RuleScope,
    Severity,
)


def _evidence(
    context: RuleContext,
    span: DiagnosticSpan,
    field_path: str,
    description: str,
) -> EvidenceRef:
    return context.evidence.resolve(
        EvidenceSelector(span_id=span.span_id, field_path=field_path),
        description=description,
    )


def _root(context: RuleContext) -> DiagnosticSpan:
    return next(span for span in context.view.spans if span.parent_span_id is None)


def _payload(span: DiagnosticSpan) -> tuple[str, dict[str, JsonValue]] | None:
    for attribute in ("tool.result", "tool.error.message"):
        raw = span.attributes.get(attribute)
        if not isinstance(raw, str):
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return f"attributes.{attribute}", value
    return None


@dataclass
class _FamilyRule:
    rule_id: str
    family_key: str
    failure_type: FailureType
    rule_version: str = "1.0"

    def evaluate(self, context: RuleContext) -> InvariantResult:
        evidence_span: DiagnosticSpan | None = None
        field_path = ""
        for span in reversed(context.view.spans):
            candidate = _payload(span)
            if candidate is not None and self.family_key in candidate[1]:
                evidence_span = span
                field_path = candidate[0]
                break
        if evidence_span is None:
            return _result(
                self,
                status=InvariantStatus.NOT_APPLICABLE,
                explanation="The trace contains no evidence for this OpsLab family.",
            )
        root = _root(context)
        if root.attributes.get("run.outcome") == "succeeded":
            return _result(
                self,
                status=InvariantStatus.PASSED,
                explanation="The OpsLab family completed with a consistent healthy state.",
            )
        return _result(
            self,
            status=InvariantStatus.FAILED,
            failure_type=self.failure_type,
            evidence=(
                _evidence(
                    context,
                    evidence_span,
                    field_path,
                    "Deterministic OpsLab failure evidence.",
                ),
                _evidence(context, root, "attributes.run.outcome", "Run outcome."),
            ),
            explanation="The OpsLab family terminated with deterministic failure evidence.",
        )


class FinalStateRule:
    rule_id = "opslab.final_state"
    rule_version = "1.0"

    def evaluate(self, context: RuleContext) -> InvariantResult:
        root = _root(context)
        if root.attributes.get("run.outcome") == "succeeded":
            return _result(
                self,
                status=InvariantStatus.PASSED,
                explanation="The OpsLab run reached its successful terminal state.",
            )
        return _result(
            self,
            status=InvariantStatus.FAILED,
            failure_type=FailureType.INVALID_FINAL_STATE,
            evidence=(
                _evidence(context, root, "attributes.run.outcome", "Run outcome."),
            ),
            explanation="The OpsLab run did not reach its successful terminal state.",
        )


def _result(
    rule: InvariantRule,
    *,
    status: InvariantStatus,
    explanation: str,
    failure_type: FailureType | None = None,
    evidence: tuple[EvidenceRef, ...] = (),
) -> InvariantResult:
    failed = status is InvariantStatus.FAILED
    return InvariantResult(
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        status=status,
        severity=Severity.ERROR if failed else Severity.INFO,
        failure_type=failure_type,
        scope=RuleScope.SUPPORTED,
        evidence=evidence,
        explanation=explanation,
        hard_failure=failed,
    )


def opslab_rules() -> tuple[InvariantRule, ...]:
    return (
        _FamilyRule(
            rule_id="opslab.timeout",
            family_key="deadline",
            failure_type=FailureType.LOOP_OR_BUDGET_EXHAUSTION,
        ),
        _FamilyRule(
            rule_id="opslab.resource",
            family_key="capacity",
            failure_type=FailureType.IGNORED_TOOL_ERROR,
        ),
        _FamilyRule(
            rule_id="opslab.concurrency",
            family_key="lease_version",
            failure_type=FailureType.INVALID_FINAL_STATE,
        ),
        _FamilyRule(
            rule_id="opslab.recovery",
            family_key="checkpoint_version",
            failure_type=FailureType.INVALID_FINAL_STATE,
        ),
        FinalStateRule(),
    )
