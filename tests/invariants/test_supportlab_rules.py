import pytest

from spanvouch.failure_types import FailureType
from spanvouch.invariants.models import InvariantRule, InvariantStatus, RuleContext
from spanvouch.invariants.supportlab import (
    FinalStateRule,
    KnownToolRule,
    StepBudgetRule,
    SubmitRefundArgumentsRule,
    SubmitRefundPolicyRule,
    supported_rules,
)
from spanvouch.trace.diagnostic_view import TraceProjector
from spanvouch.trace.evidence_catalog import EvidenceCatalog
from tests.trace.test_diagnostic_view import load_trace


def context(run_id: str) -> RuleContext:
    diagnostic_context = TraceProjector().project(load_trace(run_id))
    return RuleContext(
        view=diagnostic_context.view,
        evidence=EvidenceCatalog.from_context(diagnostic_context),
    )


@pytest.mark.parametrize(
    ("rule", "run_id", "failure_type"),
    [
        (KnownToolRule(), "wrong_tool-01", FailureType.WRONG_TOOL),
        (
            SubmitRefundArgumentsRule(),
            "invalid_argument-01",
            FailureType.INVALID_ARGUMENT,
        ),
        (
            SubmitRefundPolicyRule(),
            "policy_violation-01",
            FailureType.POLICY_VIOLATION,
        ),
        (
            StepBudgetRule(),
            "loop_or_budget_exhaustion-01",
            FailureType.LOOP_OR_BUDGET_EXHAUSTION,
        ),
        (
            FinalStateRule(),
            "invalid_final_state-01",
            FailureType.INVALID_FINAL_STATE,
        ),
    ],
)
def test_supported_rule_finds_target_failure(
    rule: InvariantRule, run_id: str, failure_type: FailureType
) -> None:
    result = rule.evaluate(context(run_id))

    assert result.status is InvariantStatus.FAILED
    assert result.failure_type is failure_type
    assert result.hard_failure is True
    assert result.evidence


@pytest.mark.parametrize("rule", supported_rules())
def test_supported_rules_pass_clean_trace(rule: InvariantRule) -> None:
    result = rule.evaluate(context("clean-01"))

    assert result.status is InvariantStatus.PASSED
    assert result.hard_failure is False


@pytest.mark.parametrize("run_id", ["policy_violation-01", "context_corruption-01"])
def test_argument_rule_does_not_overlap_policy_or_context(run_id: str) -> None:
    result = SubmitRefundArgumentsRule().evaluate(context(run_id))

    assert result.status is InvariantStatus.PASSED
    assert result.failure_type is None


def test_budget_rule_places_last_repeated_span_first() -> None:
    result = StepBudgetRule().evaluate(context("loop_or_budget_exhaustion-01"))

    assert result.evidence[0].span_id == "span-008"
