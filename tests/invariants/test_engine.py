from dataclasses import dataclass

import pytest

from spanvouch.diagnosis.evidence import EvidenceCatalog
from spanvouch.diagnosis.trace_view import DiagnosticTraceView
from spanvouch.invariants.engine import InvariantEngine
from spanvouch.invariants.models import (
    InvariantResult,
    InvariantStatus,
    RuleContext,
    RuleScope,
    Severity,
)
from tests.diagnosis.test_trace_view import load_trace


@dataclass(frozen=True)
class FakeRule:
    rule_id: str
    rule_version: str = "1.0"
    status: InvariantStatus = InvariantStatus.PASSED

    def evaluate(self, context: RuleContext) -> InvariantResult:
        assert context.view.spans
        return InvariantResult(
            rule_id=self.rule_id,
            rule_version=self.rule_version,
            status=self.status,
            severity=Severity.INFO,
            scope=RuleScope.SUPPORTED,
            explanation=f"{self.rule_id} evaluated",
        )


@dataclass(frozen=True)
class ExplodingRule:
    rule_id: str = "explode"
    rule_version: str = "1.0"

    def evaluate(self, context: RuleContext) -> InvariantResult:
        raise RuntimeError("rule bug")


def context() -> RuleContext:
    view = DiagnosticTraceView.from_trace(load_trace("clean-01"))
    return RuleContext(view=view, evidence=EvidenceCatalog.from_view(view))


def test_engine_sorts_results_and_versions_independently_of_registration_order() -> None:
    first = InvariantEngine((FakeRule("z-rule"), FakeRule("a-rule")))
    second = InvariantEngine((FakeRule("a-rule"), FakeRule("z-rule")))

    assert tuple(result.rule_id for result in first.run(context())) == (
        "a-rule",
        "z-rule",
    )
    assert first.ruleset_version == second.ruleset_version
    assert len(first.ruleset_version) == 64


def test_engine_preserves_all_rule_statuses() -> None:
    engine = InvariantEngine(
        (
            FakeRule("pass", status=InvariantStatus.PASSED),
            FakeRule("fail", status=InvariantStatus.FAILED),
            FakeRule("na", status=InvariantStatus.NOT_APPLICABLE),
        )
    )

    assert {result.status for result in engine.run(context())} == set(InvariantStatus)


def test_engine_rejects_duplicate_rule_versions() -> None:
    with pytest.raises(ValueError, match="duplicate invariant rule"):
        InvariantEngine((FakeRule("same"), FakeRule("same")))


def test_engine_does_not_hide_rule_implementation_errors() -> None:
    with pytest.raises(RuntimeError, match="rule bug"):
        InvariantEngine((ExplodingRule(),)).run(context())
