from collections import Counter
from dataclasses import dataclass
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

import pytest
from opentelemetry.trace import StatusCode

from spanvouch.contracts.trace import SpanKind, SpanStatus, TraceIR
from spanvouch.labs.supportlab.decision import (
    AgentDecision,
    DecisionContext,
    DecisionKind,
    ScriptedDecisionModel,
)
from spanvouch.labs.supportlab.graph import RunOutcome, run_support_scenario
from spanvouch.labs.supportlab.models import Order
from spanvouch.labs.supportlab.repository import build_seed_repository
from spanvouch.labs.supportlab.scenarios import FailureType, Scenario, build_scenarios
from spanvouch.labs.supportlab.tools import SupportTools
from spanvouch.observability.tracing import build_test_tracer
from spanvouch.trace.mapper import map_spans

_CUSTOMER_OBSERVATION = "customer_id='cust-001' name='Demo Customer'"
_ORDER_OBSERVATION = (
    "order_id='order-001' customer_id='cust-001' policy_id='standard' "
    "status=<OrderStatus.DELIVERED: 'delivered'> "
    "items=(OrderItem(sku='sku-red', quantity=1, unit_price=Decimal('19.99')),)"
)
_POLICY_OBSERVATION = (
    "policy_id='standard' "
    "refundable_statuses=frozenset({<OrderStatus.DELIVERED: 'delivered'>}) "
    "max_refund=Decimal('100.00') requires_approval=True"
)


@dataclass(frozen=True)
class ExpectedGraphBehavior:
    outcome: RunOutcome
    tools: tuple[str, ...]
    final_message: str | None
    observations: tuple[str, ...]
    submit_overrides: dict[str, str]
    error_type: str | None = None
    error_message: str | None = None
    error_is_ignored: bool = False


def _refund_observation(scenario_id: str) -> str:
    refund_id = uuid5(uuid5(NAMESPACE_URL, "order-001"), f"{scenario_id}-refund")
    return (
        f"refund_id='{refund_id}' order_id='order-001' amount=Decimal('19.99') "
        "reason='damaged item' "
        f"idempotency_key='{scenario_id}-refund' "
        "approved_by='reviewer@example.test'"
    )


def _expected_graph_behavior(scenario: Scenario) -> ExpectedGraphBehavior:
    failure_type = scenario.expected_failure
    clean_tools = (
        "get_customer",
        "get_order",
        "get_refund_policy",
        "calculate_refund",
        "submit_refund",
    )
    clean_observations = (
        _CUSTOMER_OBSERVATION,
        _ORDER_OBSERVATION,
        _POLICY_OBSERVATION,
        "19.99",
        _refund_observation(scenario.scenario_id),
    )
    if failure_type is FailureType.NO_FAILURE:
        return ExpectedGraphBehavior(
            RunOutcome.SUCCEEDED,
            clean_tools,
            "Refund submitted successfully.",
            clean_observations,
            {},
        )
    if failure_type is FailureType.WRONG_TOOL:
        message = "'unknown tool: get_account'"
        return ExpectedGraphBehavior(
            RunOutcome.FAILED,
            ("get_account",),
            None,
            (f"ERROR:KeyError:{message}",),
            {},
            "KeyError",
            message,
        )
    if failure_type is FailureType.LOOP_OR_BUDGET_EXHAUSTION:
        return ExpectedGraphBehavior(
            RunOutcome.STEP_LIMIT,
            ("get_order",) * 8,
            None,
            (_ORDER_OBSERVATION,) * 8,
            {},
        )
    if failure_type is FailureType.MISSING_PRECONDITION:
        return ExpectedGraphBehavior(
            RunOutcome.SUCCEEDED,
            ("get_customer", "get_order", "calculate_refund", "submit_refund"),
            "Refund submitted successfully.",
            (
                _CUSTOMER_OBSERVATION,
                _ORDER_OBSERVATION,
                "19.99",
                _refund_observation(scenario.scenario_id),
            ),
            {},
        )
    if failure_type is FailureType.INVALID_FINAL_STATE:
        return ExpectedGraphBehavior(
            RunOutcome.SUCCEEDED,
            clean_tools,
            "Refund submitted successfully without a refund record.",
            clean_observations,
            {},
        )
    error_by_failure = {
        FailureType.INVALID_ARGUMENT: (
            "amount_exceeds_calculation,amount_exceeds_policy",
            {"amount": "200.00"},
            False,
        ),
        FailureType.IGNORED_TOOL_ERROR: (
            "missing_approval",
            {"approval": "none", "ignore_error": "true"},
            True,
        ),
        FailureType.CONTEXT_CORRUPTION: (
            "customer_mismatch",
            {"customer_id": "attacker-customer"},
            False,
        ),
        FailureType.POLICY_VIOLATION: (
            "missing_approval",
            {"approval": "none"},
            False,
        ),
    }
    error_message, submit_overrides, ignored = error_by_failure[failure_type]
    return ExpectedGraphBehavior(
        RunOutcome.SUCCEEDED if ignored else RunOutcome.FAILED,
        clean_tools,
        "Refund submitted successfully." if ignored else None,
        (
            _CUSTOMER_OBSERVATION,
            _ORDER_OBSERVATION,
            _POLICY_OBSERVATION,
            "19.99",
            f"ERROR:RefundRejected:{error_message}",
        ),
        submit_overrides,
        "RefundRejected",
        error_message,
        ignored,
    )


def _expected_arguments(
    scenario: Scenario, expected: ExpectedGraphBehavior
) -> tuple[dict[str, str], ...]:
    clean_by_tool = {
        "get_customer": {"customer_id": "cust-001"},
        "get_order": {"order_id": "order-001"},
        "get_refund_policy": {"order_id": "order-001"},
        "calculate_refund": {"order_id": "order-001", "item_skus": "sku-red"},
        "submit_refund": {
            "customer_id": "cust-001",
            "order_id": "order-001",
            "amount": "19.99",
            "item_skus": "sku-red",
            "calculated_amount": "19.99",
            "reason": "damaged item",
            "idempotency_key": f"{scenario.scenario_id}-refund",
            "approval": "reviewer@example.test",
            "ignore_error": "false",
            **expected.submit_overrides,
        },
        "get_account": {
            "customer_id": "cust-001",
            "order_id": "order-001",
            "amount": "19.99",
            "item_skus": "sku-red",
            "calculated_amount": "19.99",
            "reason": "damaged item",
            "idempotency_key": f"{scenario.scenario_id}-refund",
            "approval": "reviewer@example.test",
            "ignore_error": "false",
        },
    }
    return tuple(clean_by_tool[tool_name] for tool_name in expected.tools)


def scenario_for(failure_type: FailureType) -> Scenario:
    return next(item for item in build_scenarios() if item.expected_failure is failure_type)


def test_scenario_distribution_remains_four_clean_plus_two_per_failure() -> None:
    counts = Counter(scenario.expected_failure for scenario in build_scenarios())

    assert len(build_scenarios()) == 20
    assert counts == {
        FailureType.NO_FAILURE: 4,
        FailureType.WRONG_TOOL: 2,
        FailureType.INVALID_ARGUMENT: 2,
        FailureType.MISSING_PRECONDITION: 2,
        FailureType.IGNORED_TOOL_ERROR: 2,
        FailureType.CONTEXT_CORRUPTION: 2,
        FailureType.POLICY_VIOLATION: 2,
        FailureType.LOOP_OR_BUDGET_EXHAUSTION: 2,
        FailureType.INVALID_FINAL_STATE: 2,
    }


@pytest.mark.parametrize(
    "scenario",
    sorted(build_scenarios(), key=lambda item: item.scenario_id),
    ids=lambda scenario: scenario.scenario_id,
)
@pytest.mark.asyncio
async def test_all_phase3_scenarios_have_stable_graph_behavior(scenario: Scenario) -> None:
    expected = _expected_graph_behavior(scenario)
    tracer, exporter = build_test_tracer()

    result = await run_support_scenario(
        scenario=scenario,
        tools=SupportTools(build_seed_repository()),
        decision_model=ScriptedDecisionModel(scenario),
        tracer=tracer,
    )
    finished_spans = exporter.get_finished_spans()
    trace = map_spans(scenario.scenario_id, finished_spans)
    root = next(span for span in trace.spans if span.parent_span_id is None)
    tool_spans = tuple(span for span in trace.spans if span.kind is SpanKind.TOOL)
    expected_arguments = _expected_arguments(scenario, expected)

    assert result.scenario_id == scenario.scenario_id
    assert result.outcome is expected.outcome
    assert result.steps == len(expected.tools)
    assert result.observations == expected.observations
    assert result.final_message == expected.final_message
    assert isinstance(trace, TraceIR)
    assert TraceIR.model_validate(trace.model_dump(mode="python")) == trace
    assert trace.run_id == scenario.scenario_id
    assert len(trace.spans) == len(expected.tools) + 1
    assert tuple(span.name for span in tool_spans) == expected.tools
    assert root.name == "supportlab.run"
    assert root.kind is SpanKind.AGENT
    assert root.status is (
        SpanStatus.OK if expected.outcome is RunOutcome.SUCCEEDED else SpanStatus.ERROR
    )
    assert root.attributes["scenario.id"] == scenario.scenario_id
    assert "scenario.expected_failure" not in root.attributes
    assert all(
        "expected_failure" not in key.lower() and "gold" not in key.lower()
        for span in trace.spans
        for key in span.attributes
    )
    assert root.attributes["run.outcome"] == expected.outcome.value
    assert root.attributes.get("run.final_message") == expected.final_message

    for index, (span, arguments) in enumerate(zip(tool_spans, expected_arguments, strict=True)):
        assert span.attributes["tool.name"] == span.name
        assert {
            key.removeprefix("tool.arguments."): value
            for key, value in span.attributes.items()
            if key.startswith("tool.arguments.")
        } == arguments
        if index == len(tool_spans) - 1 and expected.error_type is not None:
            assert span.status is SpanStatus.ERROR
            assert span.attributes["tool.error.type"] == expected.error_type
            assert span.attributes["tool.error.message"] == expected.error_message
            assert "tool.result" not in span.attributes
            raw_span = next(item for item in finished_spans if item.name == span.name)
            exception_event = next(
                event for event in raw_span.events if event.name == "exception"
            )
            expected_exception_type = (
                "spanvouch.labs.supportlab.tools.RefundRejected"
                if expected.error_type == "RefundRejected"
                else expected.error_type
            )
            assert exception_event.attributes is not None
            assert (
                exception_event.attributes["exception.type"]
                == expected_exception_type
            )
        else:
            assert span.status is SpanStatus.OK
            assert span.attributes["tool.result"] == expected.observations[index]
            assert "tool.error.type" not in span.attributes


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
    ("tool_name", "arguments", "error_type", "exception_type"),
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
            "decimal.InvalidOperation",
        ),
        (
            "calculate_refund",
            {"order_id": "order-001", "item_skus": "sku-red,sku-red"},
            "ValueError",
            "ValueError",
        ),
    ],
)
@pytest.mark.asyncio
async def test_expected_tool_errors_return_structured_failed_results(
    tool_name: str,
    arguments: dict[str, str],
    error_type: str,
    exception_type: str,
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
    exception_event = next(event for event in tool_span.events if event.name == "exception")
    assert exception_event.attributes is not None
    assert exception_event.attributes["exception.type"] == exception_type
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
