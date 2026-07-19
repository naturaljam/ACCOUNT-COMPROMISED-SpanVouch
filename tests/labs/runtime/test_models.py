from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from inspect import Parameter, signature
from math import inf
from typing import Self, get_type_hints

import pytest
from pydantic import ValidationError

from spanvouch.contracts.trace import SpanKind, SpanStatus, TraceIR, TraceSpan
from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.labs.runtime.models import (
    AgentAction,
    ExecutionProvenance,
    ExecutionRecord,
    ExecutionStatus,
    FrameworkId,
    LabScenario,
    RuntimeConfig,
    RuntimeFailure,
    RuntimeFailureCategory,
    RuntimeState,
    ToolObservation,
)


def _scenario() -> LabScenario:
    return LabScenario(
        scenario_id="scenario-1",
        template_id="template-1",
        domain="supportlab",
        failure_family="clean",
        user_request="Refund the damaged item.",
        parameters={"order_id": "order-1"},
        injection={"enabled": False},
        tool_contract_sha256="a" * 64,
        terminal_predicate_id="refund-created",
        allowed_evidence_selectors=("tool.name", "tool.status"),
    )


def _config() -> RuntimeConfig:
    return RuntimeConfig(
        seed=20260719,
        repetition=1,
        max_steps=8,
        timeout_seconds=5.0,
        max_retries=0,
        max_tool_calls=8,
    )


def _trace() -> TraceIR:
    started_at = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
    return TraceIR(
        trace_id="trace-1",
        run_id="run-1",
        spans=[
            TraceSpan(
                trace_id="trace-1",
                span_id="span-root",
                name="supportlab.run",
                kind=SpanKind.AGENT,
                status=SpanStatus.OK,
                started_at=started_at,
                ended_at=started_at + timedelta(seconds=2),
                attributes={"run.outcome": "succeeded"},
            )
        ],
    )


def _provenance() -> ExecutionProvenance:
    return ExecutionProvenance(
        git_commit="b" * 40,
        package_version="0.2.0",
        dependency_lock_sha256="c" * 64,
        dataset_manifest_sha256="d" * 64,
        environment_sha256="e" * 64,
        tool_versions={"supportlab": "1.0"},
        runtime_versions={"python": "3.12.10"},
        dirty_worktree=False,
    )


def _success_state() -> RuntimeState:
    observation = ToolObservation(
        tool_name="get_order",
        result={"order_id": "order-1"},
        status="ok",
        retryable=False,
    )
    return RuntimeState.initial().with_observation(observation).with_final("Refund created.")


@pytest.fixture
def record() -> ExecutionRecord:
    started_at = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
    return ExecutionRecord.from_run(
        scenario=_scenario(),
        run_config=_config(),
        framework_id=FrameworkId.LANGGRAPH,
        framework_version="0.6.7",
        trace=_trace(),
        state=_success_state(),
        status=ExecutionStatus.SUCCEEDED,
        failure=None,
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        provenance=_provenance(),
    )


def test_runtime_values_are_frozen_and_reject_unknown_fields() -> None:
    config = _config()
    with pytest.raises(ValidationError, match="frozen"):
        config.seed = 1
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RuntimeConfig.model_validate({**config.model_dump(), "unexpected": True})

    scenario = _scenario()
    with pytest.raises(ValidationError, match="frozen"):
        scenario.domain = "opslab"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        LabScenario.model_validate({**scenario.model_dump(), "gold_label": "hidden"})


def test_runtime_values_deep_freeze_hash_bound_inputs(record: ExecutionRecord) -> None:
    scenario = _scenario()
    action = AgentAction(
        kind="tool",
        tool_name="get_order",
        arguments={"filters": ["open"]},
    )

    with pytest.raises(TypeError, match="immutable"):
        scenario.parameters["order_id"] = "changed"
    with pytest.raises(TypeError, match="immutable"):
        action.arguments["filters"].append("closed")  # type: ignore[union-attr]
    with pytest.raises(TypeError, match="immutable"):
        record.provenance.tool_versions["supportlab"] = "changed"
    with pytest.raises(TypeError, match="immutable"):
        record.trace.spans.append(record.trace.spans[0])
    with pytest.raises(TypeError, match="immutable"):
        record.trace.spans[0].attributes["run.outcome"] = "changed"


@pytest.mark.parametrize(
    "forbidden",
    ("gold_label", "expected_finding", "mutation_metadata", "split_identity"),
)
def test_lab_scenario_rejects_nested_evaluator_metadata(forbidden: str) -> None:
    payload = _scenario().model_dump(mode="python")
    payload["injection"] = {forbidden: "hidden"}

    with pytest.raises(ValidationError, match="forbidden field"):
        LabScenario.model_validate(payload)


@pytest.mark.parametrize(
    ("update", "message"),
    (
        ({"repetition": 0}, "greater than or equal to 1"),
        ({"max_steps": 0}, "greater than or equal to 1"),
        ({"timeout_seconds": 0}, "greater than 0"),
        ({"max_retries": -1}, "greater than or equal to 0"),
        ({"max_tool_calls": 0}, "greater than or equal to 1"),
    ),
)
def test_runtime_config_enforces_execution_limits(
    update: dict[str, int], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        RuntimeConfig.model_validate({**_config().model_dump(), **update})


def test_runtime_config_rejects_non_finite_timeout() -> None:
    with pytest.raises(ValidationError, match="finite_number"):
        RuntimeConfig.model_validate({**_config().model_dump(), "timeout_seconds": inf})


@pytest.mark.parametrize(
    "action",
    (
        AgentAction(kind="tool", tool_name="get_order", arguments={"order_id": "order-1"}),
        AgentAction(kind="final", final_message="Refund created."),
    ),
)
def test_agent_action_accepts_only_the_matching_shape(action: AgentAction) -> None:
    assert action.kind in {"tool", "final"}


@pytest.mark.parametrize(
    "payload",
    (
        {"kind": "tool", "arguments": {}},
        {"kind": "tool", "tool_name": "get_order", "final_message": "not allowed"},
        {"kind": "final", "tool_name": "get_order", "final_message": "done"},
        {"kind": "final", "arguments": {"unexpected": True}, "final_message": "done"},
        {"kind": "final"},
    ),
)
def test_agent_action_rejects_mismatched_shapes(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentAction.model_validate(payload)


def test_tool_observation_requires_result_or_sanitized_error_by_status() -> None:
    assert ToolObservation(
        tool_name="get_order", result={"order_id": "order-1"}, status="ok", retryable=False
    ).error is None
    assert ToolObservation(
        tool_name="submit_refund",
        error={"code": "approval_required"},
        status="error",
        retryable=False,
    ).result is None

    with pytest.raises(ValidationError):
        ToolObservation(tool_name="get_order", status="ok", retryable=False)
    with pytest.raises(ValidationError):
        ToolObservation(
            tool_name="get_order",
            result={"order_id": "order-1"},
            error={"code": "unexpected"},
            status="ok",
            retryable=False,
        )


def test_runtime_state_transitions_are_immutable() -> None:
    initial = RuntimeState.initial()
    observation = ToolObservation(
        tool_name="get_order",
        result={"order_id": "order-1"},
        status="ok",
        retryable=False,
    )
    observed = initial.with_observation(observation)
    final = observed.with_final("Refund created.")

    assert initial == RuntimeState(
        step=0,
        tool_calls=0,
        observations=(),
        final_message=None,
        failure=None,
    )
    assert observed.step == 1
    assert observed.tool_calls == 1
    assert observed.observations == (observation,)
    assert final.final_message == "Refund created."
    assert observed.final_message is None

    with pytest.raises(ValueError, match="terminal"):
        final.with_observation(observation)


def test_runtime_state_records_a_single_typed_failure() -> None:
    failure = RuntimeFailure.from_message(
        category=RuntimeFailureCategory.FRAMEWORK_EXECUTION,
        code="tool_execution_failed",
        retryable=False,
        sanitized_message="RefundRejected",
    )
    failed = RuntimeState.initial().with_failure(failure)

    assert failed.failure == failure
    assert failure.error_sha256 == sha256(b"RefundRejected").hexdigest()
    assert "RefundRejected" not in failure.model_dump_json()
    with pytest.raises(ValueError, match="terminal"):
        failed.with_final("not allowed")


def test_failure_categories_are_pairwise_disjoint() -> None:
    values = {item.value for item in RuntimeFailureCategory}
    assert values == {
        "framework_execution_failure",
        "framework_incompatibility",
        "infrastructure_failure",
    }
    assert len(values) == len(RuntimeFailureCategory)


def test_execution_record_computes_canonical_hashes_and_counts(
    record: ExecutionRecord,
) -> None:
    assert record.trace_sha256 == canonical_sha256(record.trace)
    assert record.runtime_config == _config()
    assert record.runtime_config_sha256 == canonical_sha256(_config())
    assert record.steps == 1
    assert record.tool_calls == 1
    assert record.latency_seconds == 2.0
    assert record.final_message == "Refund created."


def test_execution_record_rejects_forged_trace_hash(record: ExecutionRecord) -> None:
    with pytest.raises(ValidationError, match="trace_sha256"):
        ExecutionRecord.model_validate(
            {**record.model_dump(mode="python"), "trace_sha256": "f" * 64}
        )


def test_execution_record_rejects_forged_runtime_config_hash(
    record: ExecutionRecord,
) -> None:
    with pytest.raises(ValidationError, match="runtime_config_sha256"):
        ExecutionRecord.model_validate(
            {
                **record.model_dump(mode="python"),
                "runtime_config_sha256": "f" * 64,
            }
        )


def test_execution_record_rejects_invalid_timing_and_counts(record: ExecutionRecord) -> None:
    with pytest.raises(ValidationError):
        ExecutionRecord.model_validate({**record.model_dump(mode="python"), "steps": -1})
    with pytest.raises(ValidationError, match="UTC"):
        ExecutionRecord.model_validate(
            {
                **record.model_dump(mode="python"),
                "started_at": datetime(2026, 7, 19, 8, 0),
            }
        )
    with pytest.raises(ValidationError, match="latency_seconds"):
        ExecutionRecord.model_validate(
            {**record.model_dump(mode="python"), "latency_seconds": 3.0}
        )


def test_execution_record_enforces_status_failure_consistency(
    record: ExecutionRecord,
) -> None:
    failure = RuntimeFailure.from_message(
        category=RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY,
        code="unsupported_scenario",
        retryable=False,
        sanitized_message="unsupported scenario",
    )
    with pytest.raises(ValidationError, match="succeeded"):
        ExecutionRecord.model_validate(
            {**record.model_dump(mode="python"), "failure": failure}
        )
    with pytest.raises(ValidationError, match="requires failure"):
        ExecutionRecord.model_validate(
            {
                **record.model_dump(mode="python"),
                "status": ExecutionStatus.INCOMPATIBLE,
                "final_message": None,
            }
        )
    with pytest.raises(ValidationError, match="incompatibility"):
        ExecutionRecord.model_validate(
            {
                **record.model_dump(mode="python"),
                "status": ExecutionStatus.FAILED,
                "failure": failure,
                "final_message": None,
            }
        )


def test_execution_provenance_requires_sorted_version_maps() -> None:
    payload = _provenance().model_dump(mode="python")
    with pytest.raises(ValidationError, match="sorted"):
        ExecutionProvenance.model_validate(
            {**payload, "tool_versions": {"z-tool": "1", "a-tool": "2"}}
        )
    with pytest.raises(ValidationError, match="sorted"):
        ExecutionProvenance.model_validate(
            {**payload, "runtime_versions": {"z-runtime": "1", "a-runtime": "2"}}
        )


def test_execution_record_excludes_evaluator_and_secret_fields(
    record: ExecutionRecord,
) -> None:
    serialized = record.model_dump_json()
    for forbidden in (
        "gold_label",
        "expected_finding",
        "split_identity",
        "api_key",
        "authorization",
        "raw_response",
        "prompt_text",
        "hidden_reasoning",
    ):
        assert forbidden not in serialized.lower()


def test_execution_record_rejects_forbidden_trace_attribute_names(
    record: ExecutionRecord,
) -> None:
    leaked_trace = record.trace.model_copy(
        update={
            "spans": [
                record.trace.spans[0].model_copy(
                    update={"attributes": {"authorization": "Bearer secret"}}
                )
            ]
        }
    )
    with pytest.raises(ValidationError, match="forbidden field"):
        ExecutionRecord.model_validate(
            {
                **record.model_dump(mode="python"),
                "trace": leaked_trace,
                "trace_sha256": canonical_sha256(leaked_trace),
            }
        )


def test_from_run_projects_trace_attributes_through_the_contract_allowlist() -> None:
    trace = _trace()
    projected_input = trace.model_copy(
        update={
            "spans": [
                trace.spans[0].model_copy(
                    update={
                        "attributes": {
                            "run.outcome": "succeeded",
                            "input": "full prompt text",
                            "message": "raw provider response",
                            "headers": {"authorization": "Bearer opaque-secret-123"},
                        }
                    }
                )
            ]
        }
    )
    started_at = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
    record = ExecutionRecord.from_run(
        scenario=_scenario(),
        run_config=_config(),
        framework_id=FrameworkId.LANGGRAPH,
        framework_version="0.6.7",
        trace=projected_input,
        state=_success_state(),
        status=ExecutionStatus.SUCCEEDED,
        failure=None,
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        provenance=_provenance(),
    )

    assert record.trace.spans[0].attributes == {"run.outcome": "succeeded"}
    serialized = record.model_dump_json().lower()
    assert "full prompt text" not in serialized
    assert "raw provider response" not in serialized
    assert "opaque-secret-123" not in serialized


def test_execution_record_rejects_unprojected_bypass_trace(
    record: ExecutionRecord,
) -> None:
    bypass_trace = record.trace.model_copy(
        update={
            "spans": [
                record.trace.spans[0].model_copy(
                    update={"attributes": {"message": "raw provider response"}}
                )
            ]
        }
    )
    with pytest.raises(ValidationError, match="allowlisted"):
        ExecutionRecord.model_validate(
            {
                **record.model_dump(mode="python"),
                "trace": bypass_trace,
                "trace_sha256": canonical_sha256(bypass_trace),
            }
        )


def test_execution_record_from_run_has_the_frozen_signature() -> None:
    parameters = signature(ExecutionRecord.from_run).parameters
    assert tuple(parameters) == (
        "scenario",
        "run_config",
        "framework_id",
        "framework_version",
        "trace",
        "state",
        "status",
        "failure",
        "started_at",
        "completed_at",
        "provenance",
    )
    assert all(item.kind is Parameter.KEYWORD_ONLY for item in parameters.values())
    hints = get_type_hints(ExecutionRecord.from_run)
    assert hints["scenario"] is LabScenario
    assert hints["run_config"] is RuntimeConfig
    assert hints["trace"] is TraceIR
    assert hints["state"] is RuntimeState
    assert hints["status"] is ExecutionStatus
    assert hints["failure"] == RuntimeFailure | None
    assert hints["return"] is Self
