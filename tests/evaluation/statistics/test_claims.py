import pytest

from spanvouch.evaluation.statistics.claims import (
    ClaimGateEvidence,
    HypothesisOutcome,
    evaluate_claim_gates,
)
from spanvouch.evaluation.statistics.inference import (
    ClusterBootstrapResult,
    HolmEntry,
    HolmResult,
    PairedEffect,
)
from spanvouch.evaluation.statistics.metrics import Ratio


def _effect(
    comparison: str,
    metric: str,
    estimate: float,
    lower: float,
    upper: float,
    *,
    failure_explains: bool = False,
) -> ClusterBootstrapResult:
    return ClusterBootstrapResult(
        effect=PairedEffect(
            comparison_id=comparison,
            metric=metric,
            reference_condition="b2_deepseek_shared",
            candidate_condition="candidate",
            estimate=estimate,
        ),
        seed=20260720,
        draws=1000,
        defined_draws=1000,
        undefined_draws=0,
        undefined_draw_rate=0.0,
        distinct_defined_estimates=10,
        confidence_level=0.95,
        lower=lower,
        upper=upper,
        cluster_count=10,
        row_count=40,
        operational_failure_explains_gain=failure_explains,
        claim_gate_passed=not failure_explains,
    )


def _evidence(**updates: object) -> ClaimGateEvidence:
    values: dict[str, object] = {
        "source_artifact_sha256s": ("a" * 64, "b" * 64),
        "analysis_complete": True,
        "missing_cells": 0,
        "holm": HolmResult(
            entries=(
                HolmEntry(
                    comparison_id="h2",
                    raw_p_value=0.01,
                    adjusted_p_value=0.02,
                    rank=1,
                ),
                HolmEntry(
                    comparison_id="h3",
                    raw_p_value=0.02,
                    adjusted_p_value=0.02,
                    rank=2,
                ),
            )
        ),
        "holm_alpha": 0.05,
        "coverage_loss_tolerance": 0.05,
        "langgraph_contract_valid": Ratio.from_counts(96, 100),
        "autogen_contract_valid": Ratio.from_counts(95, 100),
        "h1_completion_effect": _effect("h1", "completion", 0.0, -0.04, 0.03),
        "h2_risk_effect": _effect("h2", "risk", -0.1, -0.2, -0.01),
        "h2_coverage_effect": _effect("h2-coverage", "coverage", -0.02, -0.04, 0.0),
        "h2_framework_beneficial": {"langgraph": True, "autogen": True},
        "h3_risk_effect": _effect("h3", "risk", -0.12, -0.22, -0.02),
        "h3_coverage_effect": _effect("h3-coverage", "coverage", -0.01, -0.03, 0.01),
        "h3_joint_error_effect": -0.05,
        "h3_framework_beneficial": {"langgraph": True, "autogen": True},
        "risk_coverage_evidence_complete": True,
        "missingness_explains_gain": False,
        "opslab_risk_effect": _effect("h5", "risk", -0.05, -0.15, 0.04),
        "opslab_scope_limited": True,
    }
    values.update(updates)
    return ClaimGateEvidence.model_validate(values)


def test_h1_requires_contract_rates_and_completion_lower_bound() -> None:
    supported = evaluate_claim_gates(_evidence()).by_id("H1")
    contradicted = evaluate_claim_gates(
        _evidence(autogen_contract_valid=Ratio.from_counts(94, 100))
    ).by_id("H1")
    assert supported.outcome is HypothesisOutcome.SUPPORTED
    assert contradicted.outcome is HypothesisOutcome.CONTRADICTED


def test_h2_requires_upper_risk_ci_coverage_and_both_frameworks() -> None:
    assert evaluate_claim_gates(_evidence()).by_id("H2").outcome is HypothesisOutcome.SUPPORTED
    mixed = _evidence(h2_framework_beneficial={"langgraph": True, "autogen": False})
    assert evaluate_claim_gates(mixed).by_id("H2").outcome is HypothesisOutcome.UNRESOLVED
    excess = _evidence(
        h2_coverage_effect=_effect("h2-coverage", "coverage", -0.06, -0.08, -0.04)
    )
    assert evaluate_claim_gates(excess).by_id("H2").outcome is HypothesisOutcome.CONTRADICTED


def test_h3_additionally_requires_lower_conditional_joint_error() -> None:
    assert evaluate_claim_gates(_evidence()).by_id("H3").outcome is HypothesisOutcome.SUPPORTED
    no_joint_gain = _evidence(h3_joint_error_effect=0.01)
    assert (
        evaluate_claim_gates(no_joint_gain).by_id("H3").outcome
        is HypothesisOutcome.CONTRADICTED
    )


def test_h4_rejects_improvement_explained_by_failures_or_missingness() -> None:
    assert evaluate_claim_gates(_evidence()).by_id("H4").outcome is HypothesisOutcome.SUPPORTED
    explained = _evidence(
        h2_risk_effect=_effect(
            "h2", "risk", -0.1, -0.2, -0.01, failure_explains=True
        )
    )
    assert (
        evaluate_claim_gates(explained).by_id("H4").outcome
        is HypothesisOutcome.CONTRADICTED
    )


def test_h5_is_opslab_specific_and_uncertainty_qualified() -> None:
    decision = evaluate_claim_gates(_evidence()).by_id("H5")
    assert decision.outcome is HypothesisOutcome.SUPPORTED
    assert "opslab" in decision.scope.lower()
    assert "95%" in decision.rationale


@pytest.mark.parametrize(
    "updates",
    [
        {"analysis_complete": False},
        {"missing_cells": 1},
        {"holm": HolmResult(entries=())},
        {
            "h2_risk_effect": _effect("h2", "risk", -0.1, -0.2, -0.01).model_copy(
                update={
                    "defined_draws": 900,
                    "undefined_draws": 100,
                    "undefined_draw_rate": 0.1,
                    "claim_gate_passed": False,
                }
            )
        },
    ],
)
def test_incomplete_undefined_or_holm_failure_never_becomes_supported(
    updates: dict[str, object],
) -> None:
    report = evaluate_claim_gates(_evidence(**updates))
    assert all(item.outcome is not HypothesisOutcome.SUPPORTED for item in report.decisions)
