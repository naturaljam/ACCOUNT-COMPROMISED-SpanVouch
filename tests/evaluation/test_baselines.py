from spanvouch.evaluation.baselines import final_state_baseline, rule_only_baseline
from spanvouch.labs.supportlab.scenarios import FailureType


def test_final_state_baseline_only_detects_explicit_failures() -> None:
    prediction = final_state_baseline(outcome="failed", final_message=None)
    assert prediction.failure_type is FailureType.INVALID_FINAL_STATE


def test_rule_only_baseline_maps_policy_error() -> None:
    prediction = rule_only_baseline(
        observations=("ERROR:RefundRejected:missing_approval",),
        steps=5,
        max_steps=8,
    )
    assert prediction.failure_type is FailureType.POLICY_VIOLATION
