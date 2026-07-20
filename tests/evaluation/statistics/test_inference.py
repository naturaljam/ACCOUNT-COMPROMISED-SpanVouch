import json
from pathlib import Path

import pytest

from spanvouch.evaluation.statistics.inference import (
    exact_mcnemar,
    holm_adjust,
    paired_cluster_bootstrap,
)
from spanvouch.evaluation.statistics.metrics import ConditionObservation


def row(
    identifier: str,
    cluster: str,
    condition: str,
    *,
    accepted: bool,
    correct: bool | None,
    completion: bool = True,
    failure: str | None = None,
) -> ConditionObservation:
    return ConditionObservation(
        observation_id=identifier,
        cell_id=identifier.rsplit("-", 1)[0],
        cluster_id=cluster,
        condition_id=condition,
        framework_id="autogen" if condition == "candidate" else "langgraph",
        candidate_exists=True,
        accepted=accepted,
        correct=correct,
        confidence=0.8 if accepted else 0.2,
        completion=completion,
        operational_failure=failure,
    )


def fixture() -> dict[str, object]:
    path = Path(__file__).parent / "fixtures/known-effects.json"
    return json.loads(path.read_text("utf-8"))


def test_exact_mcnemar_matches_hand_computed_two_sided_tail() -> None:
    payload = fixture()["mcnemar"]
    result = exact_mcnemar(
        comparison_id="known-discordance",
        discordant_reference_only=payload["reference_only"],
        discordant_candidate_only=payload["candidate_only"],
    )
    assert result.discordant_total == 6
    assert result.p_value == pytest.approx(0.21875)


def test_known_effect_fixture_declares_zero_benefit_and_cluster_false_precision() -> None:
    payload = fixture()
    assert payload["zero_effect"] == 0.0
    assert payload["beneficial_b3_risk_effect"] == -1.0
    cluster = payload["cluster_repetitions"]
    assert cluster["whole_cluster_effects"] == [0.0, 0.75, 1.0]
    assert cluster["row_level_false_precision_effects"] == [0.25, 0.5]


def test_mcnemar_zero_discordance_is_exactly_one() -> None:
    result = exact_mcnemar(
        comparison_id="zero", discordant_reference_only=0,
        discordant_candidate_only=0,
    )
    assert result.p_value == 1.0


def test_holm_adjustment_is_tie_stable_and_monotone() -> None:
    raw = fixture()["holm_raw"]
    result = holm_adjust(raw)
    adjusted = {item.comparison_id: item.adjusted_p_value for item in result.entries}
    assert adjusted == {"a": 0.04, "b": 0.09, "c": 0.09, "d": 0.2}
    sorted_entries = sorted(result.entries, key=lambda item: (item.raw_p_value, item.comparison_id))
    assert [item.adjusted_p_value for item in sorted_entries] == sorted(
        item.adjusted_p_value for item in sorted_entries
    )


def test_cluster_bootstrap_is_deterministic_and_detects_beneficial_risk() -> None:
    rows: list[ConditionObservation] = []
    # Every cluster carries both conditions and both repetitions together.
    for cluster in ("template-a", "template-b", "template-c"):
        for repetition in (1, 2):
            cell = f"{cluster}-r{repetition}"
            rows.extend((
                row(f"{cell}-ref", cluster, "reference", accepted=True, correct=False),
                row(f"{cell}-cand", cluster, "candidate", accepted=True, correct=True),
            ))

    first = paired_cluster_bootstrap(
        rows,
        comparison_id="b3-minus-b2-risk",
        reference_condition="reference",
        candidate_condition="candidate",
        metric="risk",
        draws=200,
        seed=17,
    )
    second = paired_cluster_bootstrap(
        rows,
        comparison_id="b3-minus-b2-risk",
        reference_condition="reference",
        candidate_condition="candidate",
        metric="risk",
        draws=200,
        seed=17,
    )
    assert first == second
    assert first.effect.estimate == -1.0
    assert first.lower == first.upper == -1.0
    assert first.cluster_count == 3
    assert first.row_count == 12
    assert first.undefined_draw_rate == 0.0
    assert first.claim_gate_passed is True


def test_cluster_draws_carry_every_repetition_for_selected_template() -> None:
    rows = [
        row("a-r1-ref", "a", "reference", accepted=False, correct=None,
            completion=False),
        row("a-r1-cand", "a", "candidate", accepted=False, correct=None,
            completion=False),
    ]
    for repetition in (1, 2, 3):
        rows.extend((
            row(f"b-r{repetition}-ref", "b", "reference", accepted=False,
                correct=None, completion=False),
            row(f"b-r{repetition}-cand", "b", "candidate", accepted=False,
                correct=None, completion=True),
        ))
    result = paired_cluster_bootstrap(
        rows,
        comparison_id="cluster-unit",
        reference_condition="reference",
        candidate_condition="candidate",
        metric="completion",
        draws=1_000,
        seed=11,
    )
    # Whole-cluster draws can only be AA=0, AB=.75, or BB=1. Row-level
    # resampling would additionally create .25 and .50, creating false precision.
    assert result.distinct_defined_estimates == 3


def test_bootstrap_records_undefined_risk_draws_and_fails_tolerance() -> None:
    rows = (
        row("a-ref", "a", "reference", accepted=True, correct=False),
        row("a-cand", "a", "candidate", accepted=False, correct=None),
        row("b-ref", "b", "reference", accepted=True, correct=True),
        row("b-cand", "b", "candidate", accepted=True, correct=True),
    )
    result = paired_cluster_bootstrap(
        rows,
        comparison_id="undefined",
        reference_condition="reference",
        candidate_condition="candidate",
        metric="risk",
        draws=200,
        seed=3,
        undefined_tolerance=0.01,
    )
    assert result.undefined_draws > 0
    assert result.undefined_draw_rate > 0.01
    assert result.claim_gate_passed is False


def test_operational_failure_cannot_explain_apparent_risk_gain() -> None:
    rows = (
        row("a-ref", "a", "reference", accepted=True, correct=False),
        row("a-cand", "a", "candidate", accepted=False, correct=None,
            completion=False, failure="provider_failure"),
        row("b-ref", "b", "reference", accepted=True, correct=True),
        row("b-cand", "b", "candidate", accepted=True, correct=True),
    )
    result = paired_cluster_bootstrap(
        rows,
        comparison_id="failure-explains-gain",
        reference_condition="reference",
        candidate_condition="candidate",
        metric="risk",
        draws=100,
        seed=9,
        undefined_tolerance=1.0,
    )
    assert result.operational_failure_explains_gain is True
    assert result.claim_gate_passed is False


def test_equal_failure_counts_on_different_cells_fail_claim_gate() -> None:
    rows = (
        row("a-ref", "a", "reference", accepted=True, correct=False),
        row(
            "a-cand",
            "a",
            "candidate",
            accepted=False,
            correct=None,
            completion=False,
            failure="provider_failure",
        ),
        row(
            "b-ref",
            "b",
            "reference",
            accepted=False,
            correct=None,
            completion=False,
            failure="provider_failure",
        ),
        row("b-cand", "b", "candidate", accepted=True, correct=True),
    )

    result = paired_cluster_bootstrap(
        rows,
        comparison_id="informative-missingness",
        reference_condition="reference",
        candidate_condition="candidate",
        metric="risk",
        draws=100,
        seed=23,
        undefined_tolerance=1.0,
    )

    assert result.operational_failure_explains_gain is True
    assert result.claim_gate_passed is False


def test_completion_effect_is_autogen_minus_langgraph() -> None:
    rows = (
        row("a-ref", "a", "reference", accepted=False, correct=None, completion=False),
        row("a-cand", "a", "candidate", accepted=False, correct=None, completion=True),
        row("b-ref", "b", "reference", accepted=False, correct=None, completion=True),
        row("b-cand", "b", "candidate", accepted=False, correct=None, completion=True),
    )
    result = paired_cluster_bootstrap(
        rows,
        comparison_id="autogen-minus-langgraph",
        reference_condition="reference",
        candidate_condition="candidate",
        metric="completion",
        draws=100,
        seed=5,
    )
    assert result.effect.estimate == 0.5


def test_formal_default_is_ten_thousand_draws() -> None:
    rows = (
        row("a-ref", "a", "reference", accepted=True, correct=True),
        row("a-cand", "a", "candidate", accepted=True, correct=True),
    )
    result = paired_cluster_bootstrap(
        rows,
        comparison_id="default-draws",
        reference_condition="reference",
        candidate_condition="candidate",
        metric="coverage",
        seed=1,
    )
    assert result.draws == 10_000
    assert result.effect.estimate == 0.0
