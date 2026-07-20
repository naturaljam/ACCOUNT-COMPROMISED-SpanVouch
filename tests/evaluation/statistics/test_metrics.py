from decimal import Decimal

import pytest

from spanvouch.evaluation.statistics.metrics import (
    ConditionObservation,
    compute_condition_metrics,
    risk_coverage_curve,
)


def observation(identifier: str, **updates: object) -> ConditionObservation:
    payload: dict[str, object] = {
        "observation_id": identifier,
        "cell_id": identifier,
        "cluster_id": f"template-{identifier}",
        "condition_id": "b3",
        "framework_id": "langgraph",
        "candidate_exists": True,
        "accepted": False,
        "correct": None,
        "confidence": None,
        "completion": True,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_cny": Decimal("0"),
        "latency_ms": None,
    }
    payload.update(updates)
    return ConditionObservation.model_validate(payload)


def test_denominators_missingness_and_zero_accepted_risk() -> None:
    rows = (
        observation(
            "1", accepted=True, correct=False, confidence=0.9,
            family_correct=False, causal_correct=False, grounded=False,
            disagreement=True, joint_error=True, input_tokens=10,
            output_tokens=2, cost_cny=Decimal("0.10"), latency_ms=10,
        ),
        observation(
            "2", accepted=True, correct=True, confidence=0.8,
            family_correct=True, causal_correct=True, grounded=True,
            disagreement=False, joint_error=False, input_tokens=20,
            output_tokens=3, cost_cny=Decimal("0.20"), latency_ms=20,
        ),
        observation(
            "3", family_correct=True, causal_correct=False, grounded=True,
            abstained=True,
        ),
        observation(
            "4", candidate_exists=False,
            operational_failure="framework_execution_failure", completion=False,
        ),
        observation(
            "5", candidate_exists=False,
            operational_failure="infrastructure_failure", completion=False,
        ),
        observation(
            "6", operational_failure="provider_failure", completion=False,
            input_tokens=5, cost_cny=Decimal("0.05"), latency_ms=30,
        ),
        observation("7", invalid_output=True),
    )

    result = compute_condition_metrics(rows)

    assert result.scheduled_count == 7
    assert result.eligible_candidate_count == 5
    assert result.accepted_count == 2
    assert result.false_acceptance_risk.numerator == 1
    assert result.false_acceptance_risk.denominator == 2
    assert result.false_acceptance_risk.value == 0.5
    assert result.coverage.as_tuple() == (2, 5, 0.4)
    assert result.all_scheduled_sensitivity_coverage.as_tuple() == (
        2, 7, pytest.approx(2 / 7)
    )
    assert result.family_accuracy.as_tuple() == (2, 3, pytest.approx(2 / 3))
    assert result.causal_correctness.as_tuple() == (1, 3, pytest.approx(1 / 3))
    assert result.grounding.as_tuple() == (2, 3, pytest.approx(2 / 3))
    assert result.disagreement.as_tuple() == (1, 2, 0.5)
    assert result.joint_error.as_tuple() == (1, 2, 0.5)
    assert result.invalid_output.as_tuple() == (1, 5, 0.2)
    assert result.abstention.as_tuple() == (1, 5, 0.2)
    assert result.framework_failures.numerator == 1
    assert result.infrastructure_failures.numerator == 1
    assert result.provider_failures.numerator == 1
    assert result.review_required is True
    assert result.input_tokens == 35
    assert result.output_tokens == 5
    assert result.cost_cny == Decimal("0.35")
    assert result.mean_latency_ms == 20.0

    none_accepted = compute_condition_metrics((observation("only"),))
    assert none_accepted.false_acceptance_risk.as_tuple() == (0, 0, None)


def test_risk_coverage_curve_uses_unique_tied_thresholds_and_is_monotone() -> None:
    rows = (
        observation("1", accepted=True, correct=True, confidence=0.8),
        observation("2", accepted=True, correct=False, confidence=0.8),
        observation("3", accepted=True, correct=True, confidence=0.4),
        observation("4", confidence=0.2, correct=False),
    )

    points = risk_coverage_curve(rows, continuous=True)

    assert [point.threshold for point in points] == [0.0, 0.2, 0.4, 0.8, 1.0]
    coverages = [point.coverage.value for point in points]
    assert coverages == sorted(coverages, reverse=True)
    tied = next(point for point in points if point.threshold == 0.8)
    assert tied.accepted_count == 2
    assert tied.risk.as_tuple() == (1, 2, 0.5)


def test_binary_conditions_have_one_fixed_operating_point() -> None:
    rows = (
        observation("1", accepted=True, correct=True),
        observation("2"),
    )
    points = risk_coverage_curve(rows, continuous=False)
    assert len(points) == 1
    assert points[0].threshold is None
    assert points[0].fixed_operating_point is True
    assert points[0].coverage.as_tuple() == (1, 2, 0.5)


def test_observation_rejects_impossible_acceptance_and_pre_candidate_state() -> None:
    with pytest.raises(ValueError, match="accepted observation requires correctness"):
        observation("bad", accepted=True)
    with pytest.raises(ValueError, match="pre-candidate failure"):
        observation(
            "bad-2",
            candidate_exists=True,
            operational_failure="framework_incompatibility",
        )


def test_risk_coverage_curve_rejects_mixed_conditions() -> None:
    rows = (
        observation("b2", condition_id="b2"),
        observation("b3", condition_id="b3"),
    )

    with pytest.raises(ValueError, match="exactly one condition"):
        risk_coverage_curve(rows, continuous=True)


def test_mean_latency_includes_valid_zero_latency_rows() -> None:
    rows = (
        observation("not-measured"),
        observation("cache-hit", latency_ms=0.0),
        observation("provider-call", latency_ms=10.0),
    )

    assert compute_condition_metrics(rows).mean_latency_ms == 5.0
