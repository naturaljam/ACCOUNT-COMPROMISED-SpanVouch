from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from spanvouch.contracts.trace import SpanKind, SpanStatus, TraceIR, TraceSpan
from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.labs.frameworks.autogen import AutoGenRuntimeAdapter
from spanvouch.labs.frameworks.langgraph import LangGraphRuntimeAdapter
from spanvouch.labs.runtime import (
    ExecutionProvenance,
    ExecutionRecord,
    ExecutionStatus,
    ParityDimension,
    ParityResult,
    RuntimeConfig,
    RuntimeFailure,
    RuntimeFailureCategory,
    ScenarioParityValidator,
)
from spanvouch.labs.supportlab.environment import SupportLabEnvironmentRegistry
from spanvouch.labs.supportlab.runtime import build_support_lab_scenarios


@pytest.fixture
def provenance() -> ExecutionProvenance:
    return ExecutionProvenance(
        git_commit="b" * 40,
        package_version="0.2.0",
        dependency_lock_sha256="c" * 64,
        dataset_manifest_sha256="d" * 64,
        environment_sha256="e" * 64,
        tool_versions={"supportlab": "1"},
        runtime_versions={"python": "3.12"},
        dirty_worktree=False,
    )


def _config() -> RuntimeConfig:
    return RuntimeConfig(
        seed=20260719,
        repetition=1,
        max_steps=10,
        timeout_seconds=5.0,
        max_retries=0,
        max_tool_calls=10,
    )


async def _paired_records(
    provenance: ExecutionProvenance,
    scenario_id: str = "clean-01",
) -> tuple[ExecutionRecord, ExecutionRecord]:
    scenario = next(
        item
        for item in build_support_lab_scenarios()
        if item.scenario_id == scenario_id
    )
    registry = SupportLabEnvironmentRegistry()
    langgraph = await LangGraphRuntimeAdapter(
        registry, provenance=provenance
    ).execute(scenario, _config())
    autogen = await AutoGenRuntimeAdapter(registry, provenance=provenance).execute(
        scenario, _config()
    )
    return langgraph, autogen


def _changed_trace(
    record: ExecutionRecord,
    change: Any,
) -> ExecutionRecord:
    payload = record.trace.model_dump(mode="python")
    change(payload)
    trace = TraceIR.model_validate(payload)
    return ExecutionRecord.model_validate(
        {
            **record.model_dump(mode="python"),
            "trace": trace,
            "trace_sha256": canonical_sha256(trace),
        }
    )


def _change_tool_attribute(
    record: ExecutionRecord,
    key: str,
    value: str,
) -> ExecutionRecord:
    def change(payload: dict[str, Any]) -> None:
        tool = next(
            span for span in payload["spans"] if span["kind"] == SpanKind.TOOL
        )
        tool["attributes"][key] = value

    return _changed_trace(record, change)


def with_changed_tool_argument(
    record: ExecutionRecord,
    argument: str,
    value: str,
) -> ExecutionRecord:
    return _change_tool_attribute(record, f"tool.arguments.{argument}", value)


def _changed_injection_marker(record: ExecutionRecord) -> ExecutionRecord:
    trace_payload = record.trace.model_dump(mode="python")
    marker = next(
        span
        for span in trace_payload["spans"]
        if "injection.trigger.id" in span["attributes"]
    )
    marker["attributes"] = {
        "injection.trigger.id": "decision.3",
        "injection.trigger.sha256": "f" * 64,
    }
    trace = TraceIR.model_validate(trace_payload)
    return ExecutionRecord.model_validate(
        {
            **record.model_dump(mode="python"),
            "injection_trigger_id": "decision.3",
            "injection_trigger_sha256": "f" * 64,
            "trace": trace,
            "trace_sha256": canonical_sha256(trace),
        }
    )


@pytest.mark.asyncio
async def test_parity_accepts_matched_framework_records(
    provenance: ExecutionProvenance,
) -> None:
    langgraph_record, autogen_record = await _paired_records(provenance)

    result = ScenarioParityValidator().validate(langgraph_record, autogen_record)

    assert result.is_match is True
    assert result.status == "matched"
    assert result.mismatches == ()


@pytest.mark.asyncio
async def test_parity_reports_tool_argument_drift_without_raw_payloads(
    provenance: ExecutionProvenance,
) -> None:
    langgraph_record, autogen_record = await _paired_records(provenance)
    changed = with_changed_tool_argument(
        autogen_record, "order_id", "wrong-order-secret"
    )

    result = ScenarioParityValidator().validate(langgraph_record, changed)

    assert result.is_match is False
    assert result.mismatches[0].dimension is ParityDimension.TOOL_ARGUMENTS
    serialized = result.model_dump_json()
    assert "wrong-order-secret" not in serialized
    assert "order-001" not in serialized


@pytest.mark.asyncio
async def test_parity_preserves_all_nine_explicit_dimensions(
    provenance: ExecutionProvenance,
) -> None:
    langgraph_record, autogen_record = await _paired_records(provenance)
    changed_config = autogen_record.runtime_config.model_copy(
        update={"max_steps": autogen_record.runtime_config.max_steps + 1}
    )
    mutations = (
        (
            ParityDimension.SCENARIO_INPUT,
            autogen_record.model_copy(update={"scenario_input_sha256": "f" * 64}),
        ),
        (
            ParityDimension.TOOL_SEQUENCE,
            _change_tool_attribute(
                autogen_record, "tool.name", "different_safe_tool"
            ),
        ),
        (
            ParityDimension.TOOL_ARGUMENTS,
            with_changed_tool_argument(autogen_record, "order_id", "different-order"),
        ),
        (
            ParityDimension.TOOL_RESULTS,
            _change_tool_attribute(
                autogen_record, "tool.result", "different-safe-result"
            ),
        ),
        (
            ParityDimension.INJECTION_TRIGGER,
            autogen_record.model_copy(update={"injection_trigger_sha256": "f" * 64}),
        ),
        (
            ParityDimension.RUNTIME_LIMIT,
            autogen_record.model_copy(
                update={"runtime_config": changed_config}
            ),
        ),
        (
            ParityDimension.TERMINAL_PREDICATE,
            autogen_record.model_copy(update={"terminal_predicate_sha256": "f" * 64}),
        ),
        (
            ParityDimension.OUTCOME,
            autogen_record.model_copy(update={"final_message": "different outcome"}),
        ),
        (
            ParityDimension.EVIDENCE_SELECTOR,
            autogen_record.model_copy(update={"evidence_selector_sha256": "f" * 64}),
        ),
    )

    for expected, changed in mutations:
        result = ScenarioParityValidator().validate(langgraph_record, changed)
        assert tuple(item.dimension for item in result.mismatches) == (expected,)


@pytest.mark.asyncio
async def test_framework_only_workflow_spans_normalize_away(
    provenance: ExecutionProvenance,
) -> None:
    langgraph_record, autogen_record = await _paired_records(provenance)
    root = autogen_record.trace.spans[0]
    extra = TraceSpan(
        trace_id=autogen_record.trace.trace_id,
        span_id="f" * 16,
        parent_span_id=root.span_id,
        name="supportlab.decision",
        kind=SpanKind.WORKFLOW,
        status=root.status,
        started_at=root.started_at + timedelta(microseconds=1),
        ended_at=root.started_at + timedelta(microseconds=2),
        attributes={},
    )
    changed = _changed_trace(
        autogen_record,
        lambda payload: payload["spans"].append(extra.model_dump(mode="python")),
    )

    result = ScenarioParityValidator().validate(langgraph_record, changed)

    assert result.is_match is True


@pytest.mark.asyncio
async def test_semantic_workflow_spans_do_not_normalize_away(
    provenance: ExecutionProvenance,
) -> None:
    langgraph_record, autogen_record = await _paired_records(provenance)
    root = autogen_record.trace.spans[0]
    semantic = TraceSpan(
        trace_id=autogen_record.trace.trace_id,
        span_id="e" * 16,
        parent_span_id=root.span_id,
        name="supportlab.semantic-workflow",
        kind=SpanKind.WORKFLOW,
        status=SpanStatus.OK,
        started_at=root.started_at + timedelta(microseconds=1),
        ended_at=root.started_at + timedelta(microseconds=2),
        attributes={"run.final_message": "semantic terminal transition"},
    )
    trace = TraceIR.model_validate(
        {
            **autogen_record.trace.model_dump(mode="python"),
            "spans": [
                *autogen_record.trace.model_dump(mode="python")["spans"],
                semantic.model_dump(mode="python"),
            ],
        }
    )
    changed = autogen_record.model_copy(
        update={"trace": trace, "trace_sha256": canonical_sha256(trace)}
    )

    result = ScenarioParityValidator().validate(langgraph_record, changed)

    assert tuple(item.dimension for item in result.mismatches) == (
        ParityDimension.OUTCOME,
    )


@pytest.mark.asyncio
async def test_empty_semantic_workflow_spans_do_not_normalize_away(
    provenance: ExecutionProvenance,
) -> None:
    langgraph_record, autogen_record = await _paired_records(provenance)
    root = autogen_record.trace.spans[0]
    semantic = TraceSpan(
        trace_id=autogen_record.trace.trace_id,
        span_id="d" * 16,
        parent_span_id=root.span_id,
        name="supportlab.semantic-empty-transition",
        kind=SpanKind.WORKFLOW,
        status=SpanStatus.OK,
        started_at=root.started_at + timedelta(microseconds=1),
        ended_at=root.started_at + timedelta(microseconds=2),
        attributes={},
    )

    changed = _changed_trace(
        autogen_record,
        lambda payload: payload["spans"].append(semantic.model_dump(mode="python")),
    )
    result = ScenarioParityValidator().validate(langgraph_record, changed)

    assert tuple(item.dimension for item in result.mismatches) == (
        ParityDimension.OUTCOME,
    )


@pytest.mark.asyncio
async def test_tool_order_error_status_and_injection_markers_do_not_normalize_away(
    provenance: ExecutionProvenance,
) -> None:
    clean_left, clean_right = await _paired_records(provenance)
    error_left, error_right = await _paired_records(provenance, "wrong_tool-01")
    injection_left, injection_right = await _paired_records(
        provenance, "invalid_argument-01"
    )

    def reorder_tools(payload: dict[str, Any]) -> None:
        indexes = [
            index
            for index, span in enumerate(payload["spans"])
            if span["kind"] == SpanKind.TOOL
        ]
        first, second = indexes[:2]
        payload["spans"][first], payload["spans"][second] = (
            payload["spans"][second],
            payload["spans"][first],
        )

    def change_tool_status(payload: dict[str, Any]) -> None:
        tool = next(
            span for span in payload["spans"] if span["kind"] == SpanKind.TOOL
        )
        tool["status"] = SpanStatus.ERROR

    reordered = ScenarioParityValidator().validate(
        clean_left, _changed_trace(clean_right, reorder_tools)
    )
    changed_error = ScenarioParityValidator().validate(
        error_left,
        _change_tool_attribute(error_right, "tool.error.message", "changed-error"),
    )
    changed_status = ScenarioParityValidator().validate(
        clean_left, _changed_trace(clean_right, change_tool_status)
    )
    changed_injection = ScenarioParityValidator().validate(
        injection_left, _changed_injection_marker(injection_right)
    )

    assert ParityDimension.TOOL_SEQUENCE in {
        mismatch.dimension for mismatch in reordered.mismatches
    }
    assert tuple(item.dimension for item in changed_error.mismatches) == (
        ParityDimension.TOOL_RESULTS,
    )
    assert tuple(item.dimension for item in changed_status.mismatches) == (
        ParityDimension.TOOL_RESULTS,
    )
    assert tuple(item.dimension for item in changed_injection.mismatches) == (
        ParityDimension.INJECTION_TRIGGER,
    )


@pytest.mark.asyncio
async def test_typed_framework_incompatibility_is_the_only_exclusion_path(
    provenance: ExecutionProvenance,
) -> None:
    langgraph_record, autogen_record = await _paired_records(provenance)
    failure = RuntimeFailure.from_message(
        category=RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY,
        code="unsupported_framework_capability",
        retryable=False,
        sanitized_message="unsupported_framework_capability",
    )
    incompatible_record = autogen_record.model_copy(
        update={
            "status": ExecutionStatus.INCOMPATIBLE,
            "failure": failure,
            "final_message": None,
        }
    )

    result = ScenarioParityValidator().validate(
        langgraph_record, incompatible_record
    )

    assert result.status == "incompatible"
    assert result.is_match is False
    assert result.framework_incompatibility == failure
    assert result.incompatibility_code == failure.code
    assert result.mismatches == ()
    with pytest.raises(ValidationError):
        ParityResult.model_validate(
            {
                "status": "incompatible",
                "mismatches": (),
                "incompatibility_code": "free-form-exclusion",
            }
        )


def test_parity_result_rejects_every_cross_state_shape() -> None:
    mismatch = {
        "dimension": ParityDimension.OUTCOME,
        "reference_sha256": "a" * 64,
        "candidate_sha256": "b" * 64,
    }
    ordinary_failure = RuntimeFailure.from_message(
        category=RuntimeFailureCategory.FRAMEWORK_EXECUTION,
        code="framework_exception",
        retryable=False,
        sanitized_message="framework_exception",
    )
    incompatible_failure = RuntimeFailure.from_message(
        category=RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY,
        code="unsupported_capability",
        retryable=False,
        sanitized_message="unsupported_capability",
    )
    invalid_payloads = (
        {"status": "matched", "mismatches": [mismatch]},
        {
            "status": "matched",
            "framework_incompatibility": incompatible_failure,
            "incompatibility_code": incompatible_failure.code,
        },
        {"status": "mismatched", "mismatches": []},
        {
            "status": "mismatched",
            "mismatches": [mismatch],
            "framework_incompatibility": incompatible_failure,
            "incompatibility_code": incompatible_failure.code,
        },
        {
            "status": "incompatible",
            "mismatches": [mismatch],
            "framework_incompatibility": incompatible_failure,
            "incompatibility_code": incompatible_failure.code,
        },
        {
            "status": "incompatible",
            "framework_incompatibility": ordinary_failure,
            "incompatibility_code": ordinary_failure.code,
        },
        {
            "status": "incompatible",
            "framework_incompatibility": incompatible_failure,
            "incompatibility_code": "different_code",
        },
    )

    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            ParityResult.model_validate(payload)
    with pytest.raises(ValidationError, match="different hashes"):
        ParityResult.model_validate(
            {
                "status": "mismatched",
                "mismatches": [
                    {
                        **mismatch,
                        "candidate_sha256": mismatch["reference_sha256"],
                    }
                ],
            }
        )
    with pytest.raises(ValidationError):
        ParityResult.model_validate(
            {
                "status": "mismatched",
                "mismatches": [mismatch],
                "exclusion_reason": "turn mismatch into exclusion",
            }
        )


@pytest.mark.asyncio
async def test_all_twenty_supportlab_scenarios_have_safe_trace_parity(
    provenance: ExecutionProvenance,
) -> None:
    registry = SupportLabEnvironmentRegistry()
    langgraph = LangGraphRuntimeAdapter(registry, provenance=provenance)
    autogen = AutoGenRuntimeAdapter(registry, provenance=provenance)

    for scenario in build_support_lab_scenarios():
        left = await langgraph.execute(scenario, _config())
        right = await autogen.execute(scenario, _config())
        result = ScenarioParityValidator().validate(left, right)
        assert result.is_match is True, (scenario.scenario_id, result)
