from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from spanvouch.contracts.diagnosis import ProviderUsage
from spanvouch.evaluation.corpus import CorpusCell
from spanvouch.evaluation.experiments.config import ConditionId
from spanvouch.evaluation.experiments.models import (
    ConditionResult,
    ConditionStatus,
    ExperimentFailure,
    ExperimentFailureCategory,
    FailureSource,
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
