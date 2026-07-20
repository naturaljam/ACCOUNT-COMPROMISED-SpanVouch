"""Pure Phase 5 risk-coverage metrics and statistical inference."""

from spanvouch.evaluation.statistics.claims import (
    ClaimGateEvidence,
    ClaimGateReport,
    HypothesisDecision,
    HypothesisOutcome,
    evaluate_claim_gates,
)
from spanvouch.evaluation.statistics.inference import (
    ClusterBootstrapResult,
    HolmResult,
    McNemarResult,
    PairedEffect,
    exact_mcnemar,
    holm_adjust,
    paired_cluster_bootstrap,
)
from spanvouch.evaluation.statistics.metrics import (
    ConditionMetrics,
    ConditionObservation,
    RiskCoveragePoint,
    compute_condition_metrics,
    risk_coverage_curve,
)

__all__ = [
    "ClaimGateEvidence",
    "ClaimGateReport",
    "ClusterBootstrapResult",
    "ConditionMetrics",
    "ConditionObservation",
    "HolmResult",
    "HypothesisDecision",
    "HypothesisOutcome",
    "McNemarResult",
    "PairedEffect",
    "RiskCoveragePoint",
    "compute_condition_metrics",
    "exact_mcnemar",
    "evaluate_claim_gates",
    "holm_adjust",
    "paired_cluster_bootstrap",
    "risk_coverage_curve",
]
