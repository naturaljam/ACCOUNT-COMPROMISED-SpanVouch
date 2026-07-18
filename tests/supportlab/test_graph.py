from unittest.mock import patch

import pytest
from opentelemetry.trace import StatusCode

from spanvouch.contracts.trace import SpanStatus
from spanvouch.observability.tracing import build_test_tracer
from spanvouch.supportlab.decision import (
    AgentDecision,
    DecisionContext,
    DecisionKind,
    ScriptedDecisionModel,
)
from spanvouch.supportlab.graph import RunOutcome, run_support_scenario
from spanvouch.supportlab.models import Order
from spanvouch.supportlab.repository import build_seed_repository
from spanvouch.supportlab.scenarios import FailureType, Scenario, build_scenarios
from spanvouch.supportlab.tools import SupportTools
from spanvouch.trace.mapper import map_spans


def scenario_for(failure_type: FailureType) -> Scenario:
    return next(item for item in build_scenarios() if item.expected_failure is failure_type)


class FixedDecisionModel:
    def __init__(self, decision: AgentDecision) -> None:
        self._decision = decision

    async def next_decision(self, context: DecisionContext) -> AgentDecision:
        return self._decision


@pytest.mark.asyncio
async def test_clean_scenario_creates_one_refund() -> None:
    scenario = scenario_for(FailureType.NO_FAILURE)
    repository = build_seed_repository()
    tracer, exporter = build_test_tracer()

    result = await run_support_scenario(
        scenario=scenario,
        tools=SupportTools(repository),
        decision_model=ScriptedDecisionModel(scenario),
        tracer=tracer,
    )

    assert result.outcome is RunOutcome.SUCCEEDED
    assert len(await repository.list_refunds("order-001")) == 1
    finished_spans = exporter.get_finished_spans()
    run_span = next(span for span in finished_spans if span.name == "supportlab.run")
    refund_span = next(span for span in finished_spans if span.name == "submit_refund")
    assert run_span.attributes is not None
    assert refund_span.attributes is not None
    assert run_span.attributes["run.outcome"] == RunOutcome.SUCCEEDED.value
    assert run_span.status.status_code is StatusCode.OK
    assert refund_span.attributes["tool.name"] == "submit_refund"
    assert "tool.result" in refund_span.attributes
    trace = map_spans(scenario.scenario_id, finished_spans)
    mapped_run_span = next(span for span in trace.spans if span.name == "supportlab.run")
    assert mapped_run_span.status is SpanStatus.OK


@pytest.mark.asyncio
async def test_loop_scenario_stops_at_max_steps() -> None:
    scenario = scenario_for(FailureType.LOOP_OR_BUDGET_EXHAUSTION)
    tracer, exporter = build_test_tracer()

    result = await run_support_scenario(
        scenario=scenario,
        tools=SupportTools(build_seed_repository()),
        decision_model=ScriptedDecisionModel(scenario),
        tracer=tracer,
        max_steps=4,
    )

    assert result.outcome is RunOutcome.STEP_LIMIT
    assert result.steps == 4
    run_span = next(
        span for span in exporter.get_finished_spans() if span.name == "supportlab.run"
    )
    assert run_span.attributes is not None
    assert run_span.attributes["run.outcome"] == RunOutcome.STEP_LIMIT.value
    assert run_span.status.status_code is StatusCode.ERROR
    trace = map_spans(scenario.scenario_id, exporter.get_finished_spans())
    mapped_run_span = next(span for span in trace.spans if span.name == "supportlab.run")
    assert mapped_run_span.status is SpanStatus.ERROR


@pytest.mark.parametrize(
    ("tool_name", "arguments", "error_type"),
    [
        (
            "submit_refund",
            {
                "customer_id": "cust-001",
                "order_id": "order-001",
                "amount": "not-a-decimal",
                "item_skus": "sku-red",
                "reason": "damaged item",
                "idempotency_key": "malformed-decimal",
                "approval": "reviewer@example.test",
            },
            "InvalidOperation",
        ),
        (
            "calculate_refund",
            {"order_id": "order-001", "item_skus": "sku-red,sku-red"},
            "ValueError",
        ),
    ],
)
@pytest.mark.asyncio
async def test_expected_tool_errors_return_structured_failed_results(
    tool_name: str,
    arguments: dict[str, str],
    error_type: str,
) -> None:
    scenario = scenario_for(FailureType.NO_FAILURE)
    tracer, exporter = build_test_tracer()
    decision_model = FixedDecisionModel(
        AgentDecision(
            kind=DecisionKind.TOOL,
            tool_name=tool_name,
            arguments=arguments,
        )
    )

    result = await run_support_scenario(
        scenario=scenario,
        tools=SupportTools(build_seed_repository()),
        decision_model=decision_model,
        tracer=tracer,
    )

    assert result.outcome is RunOutcome.FAILED
    assert result.steps == 1
    assert result.observations[0].startswith(f"ERROR:{error_type}:")
    finished_spans = exporter.get_finished_spans()
    tool_span = next(span for span in finished_spans if span.name == tool_name)
    run_span = next(span for span in finished_spans if span.name == "supportlab.run")
    assert tool_span.attributes is not None
    assert tool_span.attributes["tool.error.type"] == error_type
    assert "tool.error.message" in tool_span.attributes
    assert tool_span.status.status_code is StatusCode.ERROR
    assert any(event.name == "exception" for event in tool_span.events)
    assert run_span.attributes is not None
    assert run_span.attributes["run.outcome"] == RunOutcome.FAILED.value
    assert run_span.status.status_code is StatusCode.ERROR
    trace = map_spans(scenario.scenario_id, finished_spans)
    mapped_run_span = next(span for span in trace.spans if span.name == "supportlab.run")
    assert mapped_run_span.status is SpanStatus.ERROR


@pytest.mark.parametrize(
    ("failure_type", "outcome", "steps", "final_message", "observation_prefix"),
    [
        (FailureType.WRONG_TOOL, RunOutcome.FAILED, 1, None, "ERROR:KeyError:"),
        (
            FailureType.INVALID_ARGUMENT,
            RunOutcome.FAILED,
            5,
            None,
            "ERROR:RefundRejected:",
        ),
        (
            FailureType.MISSING_PRECONDITION,
            RunOutcome.SUCCEEDED,
            4,
            "Refund submitted successfully.",
            "refund_id=",
        ),
        (
            FailureType.IGNORED_TOOL_ERROR,
            RunOutcome.SUCCEEDED,
            5,
            "Refund submitted successfully.",
            "ERROR:RefundRejected:",
        ),
        (
            FailureType.CONTEXT_CORRUPTION,
            RunOutcome.FAILED,
            5,
            None,
            "ERROR:RefundRejected:",
        ),
        (
            FailureType.POLICY_VIOLATION,
            RunOutcome.FAILED,
            5,
            None,
            "ERROR:RefundRejected:",
        ),
        (
            FailureType.LOOP_OR_BUDGET_EXHAUSTION,
            RunOutcome.STEP_LIMIT,
            8,
            None,
            "order_id=",
        ),
        (
            FailureType.INVALID_FINAL_STATE,
            RunOutcome.SUCCEEDED,
            5,
            "Refund submitted successfully without a refund record.",
            "refund_id=",
        ),
    ],
)
@pytest.mark.asyncio
async def test_failure_scenarios_have_stable_terminal_outputs(
    failure_type: FailureType,
    outcome: RunOutcome,
    steps: int,
    final_message: str | None,
    observation_prefix: str,
) -> None:
    scenario = scenario_for(failure_type)

    result = await run_support_scenario(
        scenario=scenario,
        tools=SupportTools(build_seed_repository()),
        decision_model=ScriptedDecisionModel(scenario),
        tracer=build_test_tracer()[0],
    )

    assert result.scenario_id == scenario.scenario_id
    assert result.outcome is outcome
    assert result.steps == steps
    assert result.final_message == final_message
    assert result.observations[-1].startswith(observation_prefix)


@pytest.mark.asyncio
async def test_submit_refund_receives_explicit_item_skus() -> None:
    scenario = scenario_for(FailureType.NO_FAILURE)
    tools = SupportTools(build_seed_repository())

    with patch.object(tools, "submit_refund", wraps=tools.submit_refund) as submit_refund:
        result = await run_support_scenario(
            scenario=scenario,
            tools=tools,
            decision_model=ScriptedDecisionModel(scenario),
            tracer=build_test_tracer()[0],
        )

    assert result.outcome is RunOutcome.SUCCEEDED
    assert submit_refund.await_count == 1
    assert submit_refund.await_args is not None
    assert submit_refund.await_args.kwargs["item_skus"] == ("sku-red",)


class FatalToolError(BaseException):
    pass


class FatalSupportTools(SupportTools):
    async def get_order(self, order_id: str) -> Order:
        raise FatalToolError(order_id)


@pytest.mark.asyncio
async def test_system_level_base_exceptions_are_not_normalized() -> None:
    scenario = scenario_for(FailureType.NO_FAILURE)
    decision_model = FixedDecisionModel(
        AgentDecision(
            kind=DecisionKind.TOOL,
            tool_name="get_order",
            arguments={"order_id": scenario.order_id},
        )
    )

    with pytest.raises(FatalToolError, match=scenario.order_id):
        await run_support_scenario(
            scenario=scenario,
            tools=FatalSupportTools(build_seed_repository()),
            decision_model=decision_model,
            tracer=build_test_tracer()[0],
        )
