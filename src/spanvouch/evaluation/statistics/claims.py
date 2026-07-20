"""Preregistered, fail-closed Phase 5 H1-H5 claim gates."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from spanvouch.contracts.versioning import SHA256_PATTERN
from spanvouch.evaluation.statistics.inference import ClusterBootstrapResult, HolmResult
from spanvouch.evaluation.statistics.metrics import Ratio


class HypothesisOutcome(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNRESOLVED = "unresolved"


class ClaimGateEvidence(BaseModel):
    """Manifest-bound statistics required to adjudicate every Phase 5 hypothesis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_artifact_sha256s: tuple[str, ...] = Field(min_length=1)
    analysis_complete: bool
    missing_cells: int = Field(ge=0)
    holm: HolmResult
    holm_alpha: float = Field(gt=0.0, lt=1.0)
    coverage_loss_tolerance: float = Field(ge=0.0, le=1.0)
    langgraph_contract_valid: Ratio
    autogen_contract_valid: Ratio
    h1_completion_effect: ClusterBootstrapResult
    h2_risk_effect: ClusterBootstrapResult
    h2_coverage_effect: ClusterBootstrapResult
    h2_framework_beneficial: dict[Literal["langgraph", "autogen"], bool]
    h3_risk_effect: ClusterBootstrapResult
    h3_coverage_effect: ClusterBootstrapResult
    h3_joint_error_effect: float | None
    h3_framework_beneficial: dict[Literal["langgraph", "autogen"], bool]
    risk_coverage_evidence_complete: bool
    missingness_explains_gain: bool
    opslab_risk_effect: ClusterBootstrapResult
    opslab_scope_limited: bool

    @model_validator(mode="after")
    def validate_evidence_identity(self) -> Self:
        if len(self.source_artifact_sha256s) != len(set(self.source_artifact_sha256s)):
            raise ValueError("source artifact hashes must be unique")
        if any(
            re.fullmatch(SHA256_PATTERN, digest) is None
            for digest in self.source_artifact_sha256s
        ):
            raise ValueError("source artifacts must use SHA-256 identity")
        required_frameworks = {"langgraph", "autogen"}
        if set(self.h2_framework_beneficial) != required_frameworks or set(
            self.h3_framework_beneficial
        ) != required_frameworks:
            raise ValueError("claim directions require both frameworks")
        return self


class HypothesisDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis_id: Literal["H1", "H2", "H3", "H4", "H5"]
    outcome: HypothesisOutcome
    rationale: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    source_artifact_sha256s: tuple[str, ...]


class ClaimGateReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decisions: tuple[HypothesisDecision, ...] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def validate_complete_order(self) -> Self:
        if tuple(item.hypothesis_id for item in self.decisions) != (
            "H1",
            "H2",
            "H3",
            "H4",
            "H5",
        ):
            raise ValueError("claim report must contain ordered H1-H5 decisions")
        return self

    def by_id(self, hypothesis_id: Literal["H1", "H2", "H3", "H4", "H5"]) -> HypothesisDecision:
        return next(item for item in self.decisions if item.hypothesis_id == hypothesis_id)


def _effect_defined(result: ClusterBootstrapResult) -> bool:
    return (
        result.effect.estimate is not None
        and result.lower is not None
        and result.upper is not None
        and result.undefined_draws == 0
    )


def _holm_passed(evidence: ClaimGateEvidence) -> bool:
    entries = {entry.comparison_id: entry for entry in evidence.holm.entries}
    return all(
        comparison in entries
        and entries[comparison].adjusted_p_value <= evidence.holm_alpha
        for comparison in ("h2", "h3")
    )


def _decision(
    hypothesis_id: Literal["H1", "H2", "H3", "H4", "H5"],
    outcome: HypothesisOutcome,
    rationale: str,
    scope: str,
    evidence: ClaimGateEvidence,
) -> HypothesisDecision:
    return HypothesisDecision(
        hypothesis_id=hypothesis_id,
        outcome=outcome,
        rationale=rationale,
        scope=scope,
        source_artifact_sha256s=evidence.source_artifact_sha256s,
    )


def evaluate_claim_gates(evidence: ClaimGateEvidence) -> ClaimGateReport:
    """Adjudicate all preregistered claims without converting missingness to support."""
    validated = ClaimGateEvidence.model_validate(evidence.model_dump(mode="python"))
    all_effects = (
        validated.h1_completion_effect,
        validated.h2_risk_effect,
        validated.h2_coverage_effect,
        validated.h3_risk_effect,
        validated.h3_coverage_effect,
        validated.opslab_risk_effect,
    )
    globally_blocked = (
        not validated.analysis_complete
        or validated.missing_cells > 0
        or not _holm_passed(validated)
        or any(not _effect_defined(effect) for effect in all_effects)
    )
    if globally_blocked:
        hypothesis_ids: tuple[Literal["H1", "H2", "H3", "H4", "H5"], ...] = (
            "H1",
            "H2",
            "H3",
            "H4",
            "H5",
        )
        return ClaimGateReport(
            decisions=tuple(
                _decision(
                    hypothesis_id,
                    HypothesisOutcome.UNRESOLVED,
                    "Incomplete, missing, undefined, or Holm-failed evidence prevents support.",
                    "OpsLab-specific" if hypothesis_id == "H5" else "preregistered Phase 5",
                    validated,
                )
                for hypothesis_id in hypothesis_ids
            )
        )

    h1_pass = (
        validated.langgraph_contract_valid.value is not None
        and validated.langgraph_contract_valid.value >= 0.95
        and validated.autogen_contract_valid.value is not None
        and validated.autogen_contract_valid.value >= 0.95
        and validated.h1_completion_effect.lower is not None
        and validated.h1_completion_effect.lower > -0.05
    )
    h1 = _decision(
        "H1",
        HypothesisOutcome.SUPPORTED if h1_pass else HypothesisOutcome.CONTRADICTED,
        "Both contract-valid rates and the paired completion lower 95% CI satisfy H1."
        if h1_pass
        else "A contract-valid rate or paired completion lower 95% CI violates H1.",
        "LangGraph and AutoGen adapter portability",
        validated,
    )

    h2_directions = tuple(validated.h2_framework_beneficial.values())
    h2_risk = validated.h2_risk_effect
    h2_coverage = validated.h2_coverage_effect.effect.estimate
    h2_thresholds = (
        h2_risk.upper is not None
        and h2_risk.upper < 0
        and h2_coverage is not None
        and h2_coverage >= -validated.coverage_loss_tolerance
        and not h2_risk.operational_failure_explains_gain
    )
    h2_outcome = (
        HypothesisOutcome.UNRESOLVED
        if len(set(h2_directions)) != 1
        else (
            HypothesisOutcome.SUPPORTED
            if h2_thresholds and all(h2_directions)
            else HypothesisOutcome.CONTRADICTED
        )
    )
    h2 = _decision(
        "H2",
        h2_outcome,
        "H2 uses the risk upper 95% CI, frozen coverage tolerance, and both frameworks.",
        "same-model verifier isolation",
        validated,
    )

    h3_directions = tuple(validated.h3_framework_beneficial.values())
    h3_risk = validated.h3_risk_effect
    h3_coverage = validated.h3_coverage_effect.effect.estimate
    h3_thresholds = (
        h3_risk.upper is not None
        and h3_risk.upper < 0
        and h3_coverage is not None
        and h3_coverage >= -validated.coverage_loss_tolerance
        and validated.h3_joint_error_effect is not None
        and validated.h3_joint_error_effect < 0
        and not h3_risk.operational_failure_explains_gain
    )
    h3_outcome = (
        HypothesisOutcome.UNRESOLVED
        if len(set(h3_directions)) != 1
        else (
            HypothesisOutcome.SUPPORTED
            if h3_thresholds and all(h3_directions)
            else HypothesisOutcome.CONTRADICTED
        )
    )
    h3 = _decision(
        "H3",
        h3_outcome,
        "H3 adds conditional joint error to risk, coverage, and both-framework direction.",
        "cross-model operational verification",
        validated,
    )

    explained = (
        validated.missingness_explains_gain
        or validated.h2_risk_effect.operational_failure_explains_gain
        or validated.h3_risk_effect.operational_failure_explains_gain
    )
    h4 = _decision(
        "H4",
        (
            HypothesisOutcome.CONTRADICTED
            if explained
            else (
                HypothesisOutcome.SUPPORTED
                if validated.risk_coverage_evidence_complete
                else HypothesisOutcome.UNRESOLVED
            )
        ),
        "Risk-coverage evidence is required and failure or missingness cannot explain gain.",
        "failure-aware risk and coverage accounting",
        validated,
    )

    opslab_estimate = validated.opslab_risk_effect.effect.estimate
    h5 = _decision(
        "H5",
        (
            HypothesisOutcome.UNRESOLVED
            if not validated.opslab_scope_limited or opslab_estimate is None
            else (
                HypothesisOutcome.SUPPORTED
                if opslab_estimate < 0
                else HypothesisOutcome.CONTRADICTED
            )
        ),
        "OpsLab direction is reported with its 95% interval and remains uncertainty-qualified.",
        "OpsLab-specific; not a broad domain generalization",
        validated,
    )
    return ClaimGateReport(decisions=(h1, h2, h3, h4, h5))
