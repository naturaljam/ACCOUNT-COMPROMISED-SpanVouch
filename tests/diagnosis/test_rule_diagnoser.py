from dataclasses import dataclass

import pytest

from afc.diagnosis.evidence import EvidenceCatalog
from afc.diagnosis.models import AbstainReason, DiagnosisStatus, EvidenceSelector
from afc.diagnosis.rule_diagnoser import RuleDiagnoser
from afc.diagnosis.trace_view import DiagnosticTraceView
from afc.failure_types import FailureType
from afc.invariants.engine import InvariantEngine
from afc.invariants.models import (
    InvariantResult,
    InvariantStatus,
    RuleContext,
    RuleScope,
    Severity,
)
from afc.invariants.supportlab import supportlab_rules
from tests.diagnosis.test_trace_view import load_trace


def inputs(run_id: str) -> tuple[DiagnosticTraceView, EvidenceCatalog]:
    view = DiagnosticTraceView.from_trace(load_trace(run_id))
    return view, EvidenceCatalog.from_view(view)


@pytest.mark.asyncio
async def test_rule_diagnoser_returns_supported_failure_with_real_evidence() -> None:
    view, evidence = inputs("invalid_argument-01")
    execution = await RuleDiagnoser(InvariantEngine(supportlab_rules())).diagnose(
        view, evidence
    )

    assert execution.decision.status is DiagnosisStatus.DIAGNOSED
    assert execution.decision.failure_type is FailureType.INVALID_ARGUMENT
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
    assert execution.decision.failure_type is FailureType.NO_FAILURE


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
