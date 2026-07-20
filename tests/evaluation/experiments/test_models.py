from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from spanvouch.contracts.diagnosis import ProviderUsage
from spanvouch.evaluation.corpus import CorpusCell
from spanvouch.evaluation.experiments.config import ConditionId, load_experiment_config
from spanvouch.evaluation.experiments.models import (
    ConditionPlan,
    ConditionResult,
    ConditionStatus,
    ExperimentFailure,
    ExperimentFailureCategory,
    ExperimentMatrixManifest,
    FailureSource,
    IneligibleCell,
    ProviderPlanStatus,
    SelectiveAction,
)
from spanvouch.labs.runtime import FrameworkId


def _cell() -> CorpusCell:
    return CorpusCell(
        domain="supportlab",
        template_id="template-1",
        scenario_id="scenario-1",
        framework_id=FrameworkId.LANGGRAPH,
        repetition=1,
        seed=20260719,
    )


def _result(**updates: object) -> ConditionResult:
    values: dict[str, object] = {
        "plan_id": "1" * 64,
        "cell": _cell(),
        "record_sha256": "2" * 64,
        "trace_sha256": "3" * 64,
        "diagnosis_sha256": "4" * 64,
        "condition_id": ConditionId.B2,
        "status": ConditionStatus.COMPLETED,
        "selective_action": SelectiveAction.ACCEPT,
        "verifier_report_sha256s": ("5" * 64,),
        "request_audit_sha256s": ("6" * 64,),
        "usage": ProviderUsage(
            input_tokens=10,
            output_tokens=4,
            total_tokens=14,
            latency_ms=2.0,
            request_id=None,
        ),
        "cost_cny": Decimal("0.02"),
        "cache_status": "miss",
        "started_at_utc": datetime(2026, 7, 20, 1, tzinfo=UTC),
        "completed_at_utc": datetime(2026, 7, 20, 1, tzinfo=UTC)
        + timedelta(seconds=1),
        "failure": None,
    }
    values.update(updates)
    return ConditionResult.model_validate(values)


def test_failure_categories_are_disjoint_and_stable() -> None:
    assert [item.value for item in ExperimentFailureCategory] == [
        "framework_execution_failure",
        "framework_incompatibility",
        "infrastructure_failure",
        "provider_failure",
        "contract_invalid",
        "diagnosis_error",
        "verification_error",
    ]


@pytest.mark.parametrize(
    "category",
    [
        ExperimentFailureCategory.FRAMEWORK_EXECUTION,
        ExperimentFailureCategory.FRAMEWORK_INCOMPATIBILITY,
        ExperimentFailureCategory.INFRASTRUCTURE,
        ExperimentFailureCategory.PROVIDER,
        ExperimentFailureCategory.CONTRACT_INVALID,
    ],
)
def test_operational_failure_cannot_carry_correctness(
    category: ExperimentFailureCategory,
) -> None:
    with pytest.raises(ValidationError, match="correctness"):
        ExperimentFailure(
            category=category,
            code="stable-code",
            source=FailureSource.PROVIDER_RUNNER,
            is_correct=False,
        )


@pytest.mark.parametrize(
    "category",
    [ExperimentFailureCategory.DIAGNOSIS, ExperimentFailureCategory.VERIFICATION],
)
def test_post_call_errors_require_evaluator_provenance_and_cannot_come_from_runner(
    category: ExperimentFailureCategory,
) -> None:
    with pytest.raises(ValidationError, match="post-call evaluator"):
        ExperimentFailure(
            category=category,
            code="judge-failed",
            source=FailureSource.PROVIDER_RUNNER,
        )
    with pytest.raises(ValidationError, match="provenance"):
        ExperimentFailure(
            category=category,
            code="judge-failed",
            source=FailureSource.POST_CALL_EVALUATOR,
        )


def test_condition_result_binds_execution_without_label_fields() -> None:
    result = _result()
    payload = result.model_dump(mode="json")
    serialized = result.model_dump_json()

    assert payload["record_sha256"] == "2" * 64
    assert payload["verifier_report_sha256s"] == ["5" * 64]
    assert payload["request_audit_sha256s"] == ["6" * 64]
    for forbidden in ("gold", "expected", "split", "other_result", "label"):
        assert forbidden not in serialized.lower()
    with pytest.raises(ValidationError):
        ConditionResult.model_validate({**payload, "gold": {"is_correct": True}})


def test_failed_result_requires_typed_failure_and_no_success_artifacts() -> None:
    failure = ExperimentFailure(
        category=ExperimentFailureCategory.PROVIDER,
        code="request-failed",
        source=FailureSource.PROVIDER_RUNNER,
    )
    failed = _result(
        status=ConditionStatus.FAILED,
        selective_action=SelectiveAction.ABSTAIN,
        verifier_report_sha256s=(),
        request_audit_sha256s=("6" * 64,),
        usage=None,
        cost_cny=None,
        failure=failure,
    )
    assert failed.failure == failure

    with pytest.raises(ValidationError, match="failure"):
        _result(status=ConditionStatus.FAILED, failure=None)
    with pytest.raises(ValidationError, match="completed"):
        _result(failure=failure)


def test_records_are_frozen_and_extra_forbidden() -> None:
    result = _result()
    with pytest.raises(ValidationError):
        ConditionResult.model_validate({**result.model_dump(), "unexpected": 1})
    with pytest.raises(ValidationError):
        result.status = ConditionStatus.FAILED  # type: ignore[misc]


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"started_at_utc": datetime(2026, 7, 20, 1)}, "must be UTC"),
        ({"verifier_report_sha256s": ("bad",)}, "SHA-256"),
        ({"verifier_report_sha256s": ("5" * 64, "5" * 64)}, "unique"),
        (
            {
                "completed_at_utc": datetime(2026, 7, 20, tzinfo=UTC),
            },
            "precedes start",
        ),
        (
            {
                "status": ConditionStatus.NOT_INVOKED_BY_POLICY,
                "cache_status": "miss",
            },
            "policy-skipped",
        ),
        (
            {
                "usage": ProviderUsage(
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                    latency_ms=1,
                    request_id="raw-id",
                )
            },
            "raw request ID",
        ),
    ],
)
def test_condition_result_rejects_temporal_hash_policy_and_identity_drift(
    updates: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _result(**updates)


def test_condition_plan_rejects_provider_and_derived_identity_drift() -> None:
    endpoint = load_experiment_config(
        Path("evals/configs/phase5-pilot.json")
    ).shared_verifier
    base = {
        "experiment_id": "phase5-test",
        "experiment_config_sha256": "1" * 64,
        "corpus_manifest_sha256": "2" * 64,
        "cell": _cell(),
        "record_sha256": "3" * 64,
        "trace_sha256": "4" * 64,
        "diagnosis_sha256": "5" * 64,
        "condition_id": ConditionId.B2,
        "prompt_version": endpoint.prompt_version,
        "provider_status": ProviderPlanStatus.REQUIRED,
        "provider": endpoint.provider,
        "model": endpoint.model,
        "generation": endpoint,
    }
    plan = ConditionPlan.from_payload(**base)
    invalid = (
        {
            **base,
            "provider_status": ProviderPlanStatus.NOT_REQUIRED,
        },
        {**base, "provider": None},
        {**base, "provider": "drifted"},
    )
    for payload in invalid:
        with pytest.raises(ValueError):
            ConditionPlan.from_payload(**payload)
    with pytest.raises(ValueError, match="plan_id does not match"):
        ConditionPlan.model_validate(
            {**plan.model_dump(mode="python"), "plan_id": "f" * 64}
        )
    with pytest.raises(ValueError, match="derived"):
        ConditionPlan.from_payload(**base, plan_id="f" * 64)


def test_matrix_manifest_rejects_every_completeness_invariant() -> None:
    plan_ids = tuple(f"{index:064x}" for index in range(1, 7))
    counts = {condition: 1 for condition in ConditionId}
    valid = ExperimentMatrixManifest(
        experiment_id="phase5-test",
        experiment_config_sha256="1" * 64,
        corpus_manifest_sha256="2" * 64,
        candidate_manifest_sha256="3" * 64,
        plan_ids=plan_ids,
        eligible_cells=(_cell(),),
        ineligible=(),
        eligible_cell_count=1,
        ineligible_cell_count=0,
        condition_counts=counts,
    )
    other = _cell().model_copy(update={"scenario_id": "other"})
    excluded = IneligibleCell(
        cell=other,
        category=ExperimentFailureCategory.FRAMEWORK_EXECUTION,
        reason_code="excluded",
    )
    base = valid.model_dump(mode="python")
    invalid = (
        {**base, "plan_ids": (plan_ids[0],) * 6},
        {**base, "plan_ids": ("bad", *plan_ids[1:])},
        {**base, "eligible_cell_count": 2},
        {**base, "ineligible_cell_count": 1},
        {**base, "eligible_cells": (_cell(), _cell()), "eligible_cell_count": 2},
        {
            **base,
            "ineligible": (excluded, excluded),
            "ineligible_cell_count": 2,
        },
        {
            **base,
            "ineligible": (
                excluded.model_copy(update={"cell": _cell()}),
            ),
            "ineligible_cell_count": 1,
        },
        {**base, "condition_counts": {ConditionId.B0: 1}},
        {**base, "condition_counts": {**counts, ConditionId.B0: 0}},
        {**base, "plan_ids": plan_ids[:-1]},
    )
    for payload in invalid:
        with pytest.raises(ValueError):
            ExperimentMatrixManifest.model_validate(payload)
