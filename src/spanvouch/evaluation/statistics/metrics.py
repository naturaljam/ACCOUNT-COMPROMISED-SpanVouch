"""Pure denominator-safe metrics for joined Phase 5 evaluation records."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

OperationalFailure = Literal[
    "framework_execution_failure",
    "framework_incompatibility",
    "infrastructure_failure",
    "provider_failure",
    "contract_invalid",
    "diagnosis_error",
    "verification_error",
]

_PRE_CANDIDATE_FAILURES = frozenset(
    {
        "framework_execution_failure",
        "framework_incompatibility",
        "infrastructure_failure",
    }
)
_POST_CANDIDATE_FAILURES = frozenset(
    {"provider_failure", "contract_invalid", "diagnosis_error", "verification_error"}
)


class Ratio(BaseModel):
    """A ratio that never discards its scientific numerator or denominator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float | None

    @model_validator(mode="after")
    def validate_ratio(self) -> Self:
        if self.numerator > self.denominator:
            raise ValueError("ratio numerator cannot exceed denominator")
        expected = None if self.denominator == 0 else self.numerator / self.denominator
        if self.value != expected:
            raise ValueError("ratio value does not match numerator and denominator")
        return self

    @classmethod
    def from_counts(cls, numerator: int, denominator: int) -> Self:
        return cls(
            numerator=numerator,
            denominator=denominator,
            value=None if denominator == 0 else numerator / denominator,
        )

    def as_tuple(self) -> tuple[int, int, float | None]:
        return self.numerator, self.denominator, self.value


class ConditionObservation(BaseModel):
    """Structural adapter boundary for a fully joined evaluated condition result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: str = Field(min_length=1)
    cell_id: str | None = Field(default=None, min_length=1)
    cluster_id: str = Field(min_length=1)
    condition_id: str = Field(min_length=1)
    framework_id: Literal["langgraph", "autogen"]
    scheduled: bool = True
    candidate_exists: bool
    accepted: bool
    correct: bool | None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    completion: bool
    operational_failure: OperationalFailure | None = None
    family_correct: bool | None = None
    causal_correct: bool | None = None
    grounded: bool | None = None
    disagreement: bool | None = None
    joint_error: bool | None = None
    invalid_output: bool = False
    abstained: bool = False
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_cny: Decimal = Field(default=Decimal("0"), ge=0)
    latency_ms: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.accepted and self.correct is None:
            raise ValueError("accepted observation requires correctness")
        if self.accepted and (
            self.operational_failure is not None or self.invalid_output or self.abstained
        ):
            raise ValueError("accepted observation cannot be failed, invalid, or abstained")
        if self.operational_failure in _PRE_CANDIDATE_FAILURES and self.candidate_exists:
            raise ValueError("pre-candidate failure cannot have a diagnosis candidate")
        if self.operational_failure in _POST_CANDIDATE_FAILURES and not self.candidate_exists:
            raise ValueError("post-candidate failure requires a diagnosis candidate")
        return self


class ConditionMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    condition_id: str
    scheduled_count: int
    eligible_candidate_count: int
    accepted_count: int
    false_acceptance_risk: Ratio
    coverage: Ratio
    all_scheduled_sensitivity_coverage: Ratio
    family_accuracy: Ratio
    causal_correctness: Ratio
    grounding: Ratio
    disagreement: Ratio
    joint_error: Ratio
    invalid_output: Ratio
    abstention: Ratio
    framework_failures: Ratio
    framework_incompatibilities: Ratio
    infrastructure_failures: Ratio
    provider_failures: Ratio
    contract_failures: Ratio
    diagnosis_failures: Ratio
    verification_failures: Ratio
    review_required: bool
    input_tokens: int
    output_tokens: int
    cost_cny: Decimal
    mean_latency_ms: float | None


class RiskCoveragePoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    threshold: float | None
    fixed_operating_point: bool
    accepted_count: int
    risk: Ratio
    coverage: Ratio


def _bool_ratio(values: list[bool]) -> Ratio:
    return Ratio.from_counts(sum(values), len(values))


def _failure_ratio(
    rows: tuple[ConditionObservation, ...],
    failure: OperationalFailure,
    *,
    denominator: int,
) -> Ratio:
    return Ratio.from_counts(
        sum(row.operational_failure == failure for row in rows), denominator
    )


def compute_condition_metrics(
    observations: tuple[ConditionObservation, ...] | list[ConditionObservation],
) -> ConditionMetrics:
    rows = tuple(
        ConditionObservation.model_validate(row.model_dump(mode="python"))
        for row in observations
        if row.scheduled
    )
    if not rows:
        raise ValueError("condition metrics require scheduled observations")
    conditions = {row.condition_id for row in rows}
    if len(conditions) != 1:
        raise ValueError("condition metrics require exactly one condition")
    eligible = tuple(row for row in rows if row.candidate_exists)
    accepted = tuple(row for row in eligible if row.accepted)
    false_acceptances = sum(row.correct is False for row in accepted)

    family = [row.family_correct for row in eligible if row.family_correct is not None]
    causal = [row.causal_correct for row in eligible if row.causal_correct is not None]
    grounding = [row.grounded for row in eligible if row.grounded is not None]
    disagreement = [row.disagreement for row in eligible if row.disagreement is not None]
    joint = [row.joint_error for row in eligible if row.joint_error is not None]
    latencies = [row.latency_ms for row in rows if row.latency_ms is not None]
    scheduled_count = len(rows)
    eligible_count = len(eligible)

    return ConditionMetrics(
        condition_id=next(iter(conditions)),
        scheduled_count=scheduled_count,
        eligible_candidate_count=eligible_count,
        accepted_count=len(accepted),
        false_acceptance_risk=Ratio.from_counts(false_acceptances, len(accepted)),
        coverage=Ratio.from_counts(len(accepted), eligible_count),
        all_scheduled_sensitivity_coverage=Ratio.from_counts(
            len(accepted), scheduled_count
        ),
        family_accuracy=_bool_ratio(family),
        causal_correctness=_bool_ratio(causal),
        grounding=_bool_ratio(grounding),
        disagreement=_bool_ratio(disagreement),
        joint_error=_bool_ratio(joint),
        invalid_output=Ratio.from_counts(
            sum(row.invalid_output for row in eligible), eligible_count
        ),
        abstention=Ratio.from_counts(
            sum(row.abstained for row in eligible), eligible_count
        ),
        framework_failures=_failure_ratio(
            rows, "framework_execution_failure", denominator=scheduled_count
        ),
        framework_incompatibilities=_failure_ratio(
            rows, "framework_incompatibility", denominator=scheduled_count
        ),
        infrastructure_failures=_failure_ratio(
            rows, "infrastructure_failure", denominator=scheduled_count
        ),
        provider_failures=_failure_ratio(
            rows, "provider_failure", denominator=eligible_count
        ),
        contract_failures=_failure_ratio(
            rows, "contract_invalid", denominator=eligible_count
        ),
        diagnosis_failures=_failure_ratio(
            rows, "diagnosis_error", denominator=eligible_count
        ),
        verification_failures=_failure_ratio(
            rows, "verification_error", denominator=eligible_count
        ),
        review_required=any(
            row.operational_failure in _POST_CANDIDATE_FAILURES for row in eligible
        ),
        input_tokens=sum(row.input_tokens for row in rows),
        output_tokens=sum(row.output_tokens for row in rows),
        cost_cny=sum((row.cost_cny for row in rows), start=Decimal("0")),
        mean_latency_ms=(
            None if not latencies else sum(latencies) / len(latencies)
        ),
    )


def risk_coverage_curve(
    observations: tuple[ConditionObservation, ...] | list[ConditionObservation],
    *,
    continuous: bool,
) -> tuple[RiskCoveragePoint, ...]:
    rows = tuple(
        ConditionObservation.model_validate(row.model_dump(mode="python"))
        for row in observations
        if row.scheduled
    )
    if len({row.condition_id for row in rows}) != 1:
        raise ValueError("risk-coverage curve requires exactly one condition")
    eligible = tuple(row for row in rows if row.candidate_exists)
    if not eligible:
        raise ValueError("risk-coverage curve requires eligible candidates")
    if not continuous:
        accepted = tuple(row for row in eligible if row.accepted)
        return (
            RiskCoveragePoint(
                threshold=None,
                fixed_operating_point=True,
                accepted_count=len(accepted),
                risk=Ratio.from_counts(
                    sum(row.correct is False for row in accepted), len(accepted)
                ),
                coverage=Ratio.from_counts(len(accepted), len(eligible)),
            ),
        )

    thresholds = sorted(
        {0.0, 1.0, *(row.confidence for row in eligible if row.confidence is not None)}
    )
    points: list[RiskCoveragePoint] = []
    for threshold in thresholds:
        accepted = tuple(
            row
            for row in eligible
            if row.confidence is not None
            and row.confidence >= threshold
            and row.correct is not None
            and row.operational_failure is None
            and not row.invalid_output
            and not row.abstained
        )
        points.append(
            RiskCoveragePoint(
                threshold=threshold,
                fixed_operating_point=False,
                accepted_count=len(accepted),
                risk=Ratio.from_counts(
                    sum(row.correct is False for row in accepted), len(accepted)
                ),
                coverage=Ratio.from_counts(len(accepted), len(eligible)),
            )
        )
    return tuple(points)
