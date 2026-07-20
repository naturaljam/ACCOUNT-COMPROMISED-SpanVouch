"""Deterministic stdlib-only inference for paired Phase 5 experiments."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from spanvouch.evaluation.statistics.metrics import ConditionObservation

MetricName = Literal["completion", "risk", "coverage"]
_POST_CANDIDATE_FAILURES = frozenset(
    {"provider_failure", "contract_invalid", "diagnosis_error", "verification_error"}
)


class PairedEffect(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    comparison_id: str = Field(min_length=1)
    metric: MetricName
    reference_condition: str = Field(min_length=1)
    candidate_condition: str = Field(min_length=1)
    estimate: float | None


class ClusterBootstrapResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    effect: PairedEffect
    seed: int
    draws: int = Field(ge=1)
    defined_draws: int = Field(ge=0)
    undefined_draws: int = Field(ge=0)
    undefined_draw_rate: float = Field(ge=0.0, le=1.0)
    distinct_defined_estimates: int = Field(ge=0)
    confidence_level: float = Field(gt=0.0, lt=1.0)
    lower: float | None
    upper: float | None
    percentile_method: Literal["nearest_rank"] = "nearest_rank"
    cluster_count: int = Field(ge=1)
    row_count: int = Field(ge=1)
    operational_failure_explains_gain: bool
    claim_gate_passed: bool

    @model_validator(mode="after")
    def validate_draw_counts(self) -> Self:
        if self.defined_draws + self.undefined_draws != self.draws:
            raise ValueError("bootstrap draw counts do not sum to draws")
        if self.undefined_draw_rate != self.undefined_draws / self.draws:
            raise ValueError("undefined draw rate does not match draw counts")
        if self.defined_draws == 0 and (self.lower is not None or self.upper is not None):
            raise ValueError("undefined bootstrap cannot have an interval")
        if self.defined_draws > 0 and (self.lower is None or self.upper is None):
            raise ValueError("defined bootstrap requires an interval")
        return self


class McNemarResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    comparison_id: str = Field(min_length=1)
    discordant_reference_only: int = Field(ge=0)
    discordant_candidate_only: int = Field(ge=0)
    discordant_total: int = Field(ge=0)
    p_value: float = Field(ge=0.0, le=1.0)
    method: Literal["exact_two_sided_binomial"] = "exact_two_sided_binomial"


class HolmEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    comparison_id: str = Field(min_length=1)
    raw_p_value: float = Field(ge=0.0, le=1.0)
    adjusted_p_value: float = Field(ge=0.0, le=1.0)
    rank: int = Field(ge=1)


class HolmResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: tuple[HolmEntry, ...]
    method: Literal["holm"] = "holm"


def exact_mcnemar(
    *,
    comparison_id: str,
    discordant_reference_only: int,
    discordant_candidate_only: int,
) -> McNemarResult:
    if discordant_reference_only < 0 or discordant_candidate_only < 0:
        raise ValueError("discordant counts must be non-negative")
    total = discordant_reference_only + discordant_candidate_only
    if total == 0:
        p_value = 1.0
    else:
        lower = min(discordant_reference_only, discordant_candidate_only)
        tail = sum(math.comb(total, index) for index in range(lower + 1)) / (2**total)
        p_value = min(1.0, 2.0 * tail)
    return McNemarResult(
        comparison_id=comparison_id,
        discordant_reference_only=discordant_reference_only,
        discordant_candidate_only=discordant_candidate_only,
        discordant_total=total,
        p_value=p_value,
    )


def holm_adjust(raw_p_values: Mapping[str, float]) -> HolmResult:
    if not raw_p_values:
        raise ValueError("Holm adjustment requires at least one comparison")
    for comparison_id, value in raw_p_values.items():
        if not comparison_id or not 0.0 <= value <= 1.0:
            raise ValueError("invalid Holm comparison or p-value")
    ordered = sorted(raw_p_values.items(), key=lambda item: (item[1], item[0]))
    count = len(ordered)
    monotone = 0.0
    entries: list[HolmEntry] = []
    for index, (comparison_id, raw) in enumerate(ordered):
        monotone = max(monotone, min(1.0, (count - index) * raw))
        entries.append(
            HolmEntry(
                comparison_id=comparison_id,
                raw_p_value=raw,
                adjusted_p_value=monotone,
                rank=index + 1,
            )
        )
    return HolmResult(entries=tuple(sorted(entries, key=lambda item: item.comparison_id)))


def _cell_id(row: ConditionObservation) -> str:
    return row.cell_id or row.observation_id


def _matched_rows(
    rows: Sequence[ConditionObservation],
    reference_condition: str,
    candidate_condition: str,
) -> tuple[ConditionObservation, ...]:
    references = {
        _cell_id(row)
        for row in rows
        if row.condition_id == reference_condition
    }
    candidates = {
        _cell_id(row)
        for row in rows
        if row.condition_id == candidate_condition
    }
    matched = references & candidates
    if not matched:
        raise ValueError("paired inference requires matched condition cells")
    selected = tuple(
        row
        for row in rows
        if _cell_id(row) in matched
        and row.condition_id in {reference_condition, candidate_condition}
    )
    for cell in matched:
        for condition in (reference_condition, candidate_condition):
            count = sum(
                _cell_id(row) == cell and row.condition_id == condition
                for row in selected
            )
            if count != 1:
                raise ValueError("paired inference requires one row per cell and condition")
    return selected


def _condition_value(
    rows: Sequence[ConditionObservation], condition: str, metric: MetricName
) -> float | None:
    selected = [row for row in rows if row.condition_id == condition]
    if not selected:
        return None
    if metric == "completion":
        return sum(row.completion for row in selected) / len(selected)
    eligible = [row for row in selected if row.candidate_exists]
    if metric == "coverage":
        return None if not eligible else sum(row.accepted for row in eligible) / len(eligible)
    accepted = [row for row in eligible if row.accepted]
    if not accepted:
        return None
    return sum(row.correct is False for row in accepted) / len(accepted)


def _effect(
    rows: Sequence[ConditionObservation],
    reference_condition: str,
    candidate_condition: str,
    metric: MetricName,
) -> float | None:
    reference = _condition_value(rows, reference_condition, metric)
    candidate = _condition_value(rows, candidate_condition, metric)
    if reference is None or candidate is None:
        return None
    return candidate - reference


def _nearest_rank(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile requires defined values")
    ordered = sorted(values)
    rank = max(1, math.ceil(probability * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _operational_failure_explains_gain(
    rows: Sequence[ConditionObservation],
    reference_condition: str,
    candidate_condition: str,
    metric: MetricName,
    estimate: float | None,
) -> bool:
    if metric != "risk" or estimate is None or estimate >= 0:
        return False
    reference_failures = {
        _cell_id(row): row.operational_failure
        for row in rows
        if row.condition_id == reference_condition
        and row.operational_failure in _POST_CANDIDATE_FAILURES
    }
    candidate_failures = {
        _cell_id(row): row.operational_failure
        for row in rows
        if row.condition_id == candidate_condition
        and row.operational_failure in _POST_CANDIDATE_FAILURES
    }
    return candidate_failures != reference_failures


def paired_cluster_bootstrap(
    observations: Sequence[ConditionObservation],
    *,
    comparison_id: str,
    reference_condition: str,
    candidate_condition: str,
    metric: MetricName,
    seed: int,
    draws: int = 10_000,
    confidence_level: float = 0.95,
    undefined_tolerance: float = 0.05,
) -> ClusterBootstrapResult:
    if draws < 1:
        raise ValueError("bootstrap draws must be positive")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be between zero and one")
    if not 0.0 <= undefined_tolerance <= 1.0:
        raise ValueError("undefined_tolerance must be between zero and one")
    validated = tuple(
        ConditionObservation.model_validate(row.model_dump(mode="python"))
        for row in observations
    )
    matched = _matched_rows(validated, reference_condition, candidate_condition)
    clusters: dict[str, list[ConditionObservation]] = defaultdict(list)
    for row in matched:
        clusters[row.cluster_id].append(row)
    cluster_ids = sorted(clusters)
    generator = random.Random(seed)
    estimates: list[float] = []
    undefined = 0
    for _ in range(draws):
        sampled: list[ConditionObservation] = []
        for cluster_id in generator.choices(cluster_ids, k=len(cluster_ids)):
            sampled.extend(clusters[cluster_id])
        estimate = _effect(
            sampled, reference_condition, candidate_condition, metric
        )
        if estimate is None:
            undefined += 1
        else:
            estimates.append(estimate)
    point = _effect(matched, reference_condition, candidate_condition, metric)
    failure_explains = _operational_failure_explains_gain(
        matched,
        reference_condition,
        candidate_condition,
        metric,
        point,
    )
    alpha = 1.0 - confidence_level
    lower = None if not estimates else _nearest_rank(estimates, alpha / 2.0)
    upper = None if not estimates else _nearest_rank(estimates, 1.0 - alpha / 2.0)
    undefined_rate = undefined / draws
    return ClusterBootstrapResult(
        effect=PairedEffect(
            comparison_id=comparison_id,
            metric=metric,
            reference_condition=reference_condition,
            candidate_condition=candidate_condition,
            estimate=point,
        ),
        seed=seed,
        draws=draws,
        defined_draws=len(estimates),
        undefined_draws=undefined,
        undefined_draw_rate=undefined_rate,
        distinct_defined_estimates=len(set(estimates)),
        confidence_level=confidence_level,
        lower=lower,
        upper=upper,
        cluster_count=len(cluster_ids),
        row_count=len(matched),
        operational_failure_explains_gain=failure_explains,
        claim_gate_passed=(
            point is not None
            and undefined_rate <= undefined_tolerance
            and not failure_explains
        ),
    )
