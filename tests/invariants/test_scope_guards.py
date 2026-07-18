from pathlib import Path

import pytest

from spanvouch.contracts.diagnosis import DiagnosisStatus
from spanvouch.evaluation.diagnosis_labels import load_diagnosis_labels
from spanvouch.labs.supportlab.invariants import unsupported_guards
from spanvouch.trace.diagnostic_view import TraceProjector
from spanvouch.trace.evidence_catalog import EvidenceCatalog
from spanvouch.verification.invariants import InvariantStatus, RuleContext, RuleScope
from tests.trace.test_diagnostic_view import load_trace

LABELS = Path("evals/datasets/supportlab-v1/diagnosis-labels-v1.jsonl")


def context(run_id: str) -> RuleContext:
    diagnostic_context = TraceProjector().project(load_trace(run_id))
    return RuleContext(
        view=diagnostic_context.view,
        evidence=EvidenceCatalog.from_context(diagnostic_context),
    )


@pytest.mark.parametrize(
    "run_id",
    ["missing_precondition-01", "ignored_tool_error-01", "context_corruption-01"],
)
def test_each_unsupported_family_triggers_a_scope_guard(run_id: str) -> None:
    results = tuple(rule.evaluate(context(run_id)) for rule in unsupported_guards())
    failed = tuple(result for result in results if result.status is InvariantStatus.FAILED)

    assert len(failed) == 1
    assert failed[0].scope is RuleScope.UNSUPPORTED_GUARD
    assert failed[0].failure_type is None
    assert failed[0].hard_failure is True
    assert failed[0].evidence


def test_scope_guards_do_not_trigger_on_supported_cohort() -> None:
    labels = load_diagnosis_labels(LABELS)
    supported_run_ids = tuple(
        label.run_id
        for label in labels
        if label.expected_status is not DiagnosisStatus.ABSTAINED
    )

    for run_id in supported_run_ids:
        results = tuple(rule.evaluate(context(run_id)) for rule in unsupported_guards())
        assert all(result.status is not InvariantStatus.FAILED for result in results), run_id
