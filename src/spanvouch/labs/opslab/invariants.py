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
        payload: dict[str, JsonValue] | None = None
        for span in reversed(context.view.spans):
            candidate = _payload(span)
            if candidate is not None and self.family_key in candidate[1]:
                evidence_span = span
                field_path = candidate[0]
                payload = candidate[1]
                break
        if evidence_span is None or payload is None:
            return _result(
                self,
                status=InvariantStatus.NOT_APPLICABLE,
                explanation="The trace contains no evidence for this OpsLab family.",
            )
        root = _root(context)
        outcome = root.attributes.get("run.outcome")
        family = self.rule_id.removeprefix("opslab.")
        if _healthy_semantics(family, payload, outcome):
            return _result(
                self,
                status=InvariantStatus.PASSED,
                explanation="The OpsLab family completed with a consistent healthy state.",
            )
        recognized = _fault_semantics(family, evidence_span.name, payload, outcome)
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
            explanation=(
                "The OpsLab family has a recognized deterministic fault state."
                if recognized
                else "The OpsLab family has a semantically inconsistent state."
            ),
        )


def _healthy_semantics(
    family: str,
    payload: dict[str, JsonValue],
    outcome: JsonValue | None,
) -> bool:
    if outcome != "succeeded":
        return False
    if family == "timeout":
        return _matches(
            payload,
            {
                "attempts": 1,
                "retry_policy": "bounded-2",
                "backoff": 0,
                "upstream_calls": 1,
            },
        )
    if family == "resource":
        return _matches(
            payload,
            {
                "remaining_tokens": 0,
                "rejection": False,
                "degradation_result": "healthy-path",
            },
        )
    if family == "concurrency":
        return _matches(
            payload,
            {"wait_for_edges": [], "acquisition_result": "committed"},
        )
    return _matches(
        payload,
        {
            "current_checkpoint_version": 2,
            "loaded_checkpoint_version": 2,
            "replay_count": 1,
            "effect_count": 1,
            "checkpoint_state_hash": "state-applied",
            "current_state_hash": "state-applied",
            "state_hash_match": True,
        },
    )


def _fault_semantics(
    family: str,
    operation: str,
    payload: dict[str, JsonValue],
    outcome: JsonValue | None,
) -> bool:
    if family == "timeout":
        policy = payload.get("retry_policy")
        attempts = payload.get("attempts")
        calls = payload.get("upstream_calls")
        backoff = payload.get("backoff")
        return (
            outcome == "failed"
            and policy == "none"
            and attempts == calls == 1
            and backoff == 0
        ) or (
            outcome == "step_limit"
            and policy == "unbounded"
            and isinstance(attempts, int)
            and attempts >= 2
            and calls == attempts
            and backoff == attempts
        ) or (
            outcome == "failed"
            and policy == "bounded-3"
            and attempts == calls == 3
            and backoff == 6
        )
    if family == "resource":
        if outcome != "failed" or payload.get("capacity") != 2:
            return False
        return (
            operation == "reserve-token"
            and _matches(
                payload,
                {
                    "remaining_tokens": 2,
                    "rejection": True,
                    "degradation_result": "not-needed",
                },
            )
        ) or (
            operation == "perform-work"
            and _matches(
                payload,
                {
                    "remaining_tokens": 1,
                    "rejection": True,
                    "degradation_result": "not-needed",
                },
            )
        ) or (
            operation == "apply-degradation"
            and _matches(
                payload,
                {
                    "remaining_tokens": 0,
                    "rejection": False,
                    "degradation_result": "missing",
                },
            )
        )
    if family == "concurrency":
        edges = payload.get("wait_for_edges")
        if outcome != "failed" or payload.get("acquisition_result") != "blocked":
            return False
        single_wait = [["worker-a", "worker-b"]]
        cycle = [*single_wait, ["worker-b", "worker-a"]]
        return (
            operation in {"renew-lease", "acquire-alpha"} and edges == single_wait
        ) or (operation == "acquire-beta" and edges == cycle)
    if outcome != "failed":
        return False
    return (
        operation == "load-checkpoint"
        and _matches(
            payload,
            {
                "current_checkpoint_version": 2,
                "loaded_checkpoint_version": 1,
                "state_hash_match": False,
            },
        )
    ) or (
        operation == "apply-operation"
        and _matches(payload, {"replay_count": 2, "effect_count": 1})
    ) or (
        operation == "resume-workflow"
        and _matches(
            payload,
            {
                "checkpoint_state_hash": "state-applied",
                "current_state_hash": "state-drifted",
                "state_hash_match": False,
            },
        )
    )


def _matches(
    payload: dict[str, JsonValue],
    required: dict[str, JsonValue],
) -> bool:
    return all(payload.get(key) == value for key, value in required.items())


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
