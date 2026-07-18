from dataclasses import dataclass

import pytest

from spanvouch.contracts.diagnosis import AbstainReason, DiagnosisStatus, EvidenceSelector
from spanvouch.contracts.trace import DiagnosticContext
from spanvouch.diagnosis.rule_diagnoser import RuleDiagnoser
from spanvouch.failure_types import FailureType
from spanvouch.labs.supportlab.invariants import supportlab_rules
from spanvouch.trace.diagnostic_view import TraceProjector
from spanvouch.trace.evidence_catalog import EvidenceCatalog
from spanvouch.verification.invariant_engine import InvariantEngine
from spanvouch.verification.invariants import (
    InvariantResult,
    InvariantStatus,
    RuleContext,
    RuleScope,
    Severity,
)
from tests.trace.test_diagnostic_view import load_trace


def inputs(run_id: str) -> tuple[DiagnosticContext, EvidenceCatalog]:
    context = TraceProjector().project(load_trace(run_id))
    return context, EvidenceCatalog.from_context(context)


@pytest.mark.asyncio
async def test_rule_diagnoser_returns_supported_failure_with_real_evidence() -> None:
    view, evidence = inputs("invalid_argument-01")
    execution = await RuleDiagnoser(InvariantEngine(supportlab_rules())).diagnose(
        view, evidence
    )

    assert execution.decision.status is DiagnosisStatus.DIAGNOSED
    assert execution.decision.failure_type == FailureType.INVALID_ARGUMENT
    assert execution.decision.critical_span_ids[0] == "span-005"
    assert execution.decision.evidence
    assert execution.provenance.ruleset_version


@pytest.mark.asyncio
async def test_rule_diagnoser_abstains_when_unsupported_guard_fires() -> None:
    view, evidence = inputs("ignored_tool_error-01")
    execution = await RuleDiagnoser(InvariantEngine(supportlab_rules())).diagnose(
        view, evidence
    )

    assert execution.decision.status is DiagnosisStatus.ABSTAINED
    assert execution.decision.abstain_reason is AbstainReason.UNSUPPORTED_FAILURE_TYPE


@pytest.mark.asyncio
async def test_rule_diagnoser_returns_no_failure_for_clean_trace() -> None:
    view, evidence = inputs("clean-01")
    execution = await RuleDiagnoser(InvariantEngine(supportlab_rules())).diagnose(
        view, evidence
    )

    assert execution.decision.status is DiagnosisStatus.NO_FAILURE
    assert execution.decision.failure_type == FailureType.NO_FAILURE


@dataclass(frozen=True)
class AlwaysFailureRule:
    rule_id: str
    failure_type: FailureType
    rule_version: str = "1.0"

    def evaluate(self, context: RuleContext) -> InvariantResult:
        evidence = context.evidence.resolve(
            EvidenceSelector(span_id="span-001", field_path="name"),
            description="forced finding",
        )
        return InvariantResult(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            status=InvariantStatus.FAILED,
            severity=Severity.ERROR,
            failure_type=self.failure_type,
            scope=RuleScope.SUPPORTED,
            evidence=(evidence,),
            explanation="forced finding",
            hard_failure=True,
        )


@pytest.mark.asyncio
async def test_rule_diagnoser_abstains_on_conflicting_supported_findings() -> None:
    engine = InvariantEngine(
        (
            AlwaysFailureRule("first", FailureType.WRONG_TOOL),
            AlwaysFailureRule("second", FailureType.POLICY_VIOLATION),
        )
    )
    view, evidence = inputs("wrong_tool-01")

    execution = await RuleDiagnoser(engine).diagnose(view, evidence)

    assert execution.decision.status is DiagnosisStatus.ABSTAINED
    assert execution.decision.abstain_reason is AbstainReason.AMBIGUOUS_FINDINGS


@pytest.mark.asyncio
async def test_rule_diagnoser_abstains_when_failed_run_has_no_finding() -> None:
    view, evidence = inputs("wrong_tool-01")

    execution = await RuleDiagnoser(InvariantEngine(())).diagnose(view, evidence)

    assert execution.decision.status is DiagnosisStatus.ABSTAINED
    assert execution.decision.abstain_reason is AbstainReason.INSUFFICIENT_EVIDENCE
