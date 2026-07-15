from decimal import Decimal
from enum import StrEnum
from typing import Any, TypedDict, cast

from langgraph.graph import END, StateGraph
from opentelemetry.trace import Status, StatusCode, Tracer
from pydantic import BaseModel, ConfigDict

from afc.supportlab.decision import DecisionContext, DecisionKind, DecisionModel
from afc.supportlab.policy import Approval
from afc.supportlab.scenarios import Scenario
from afc.supportlab.tools import RefundRejected, SupportTools


class RunOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STEP_LIMIT = "step_limit"


class SupportRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scenario_id: str
    outcome: RunOutcome
    steps: int
    observations: tuple[str, ...]
    final_message: str | None


class SupportState(TypedDict):
    step: int
    observations: list[str]
    next_tool: str | None
    next_arguments: dict[str, str]
    final_message: str | None
    outcome: str | None


async def run_support_scenario(
    *,
    scenario: Scenario,
    tools: SupportTools,
    decision_model: DecisionModel,
    tracer: Tracer,
    max_steps: int = 8,
) -> SupportRunResult:
    async def decide(state: SupportState) -> dict[str, Any]:
        if state["step"] >= max_steps:
            return {"outcome": RunOutcome.STEP_LIMIT.value}
        decision = await decision_model.next_decision(
            DecisionContext(step=state["step"], observations=tuple(state["observations"]))
        )
        if decision.kind is DecisionKind.FINAL:
            return {
                "final_message": decision.message,
                "outcome": RunOutcome.SUCCEEDED.value,
            }
        return {"next_tool": decision.tool_name, "next_arguments": decision.arguments}

    async def execute(state: SupportState) -> dict[str, Any]:
        tool_name = state["next_tool"]
        arguments = state["next_arguments"]
        assert tool_name is not None
        with tracer.start_as_current_span(
            tool_name,
            attributes={
                "openinference.span.kind": "TOOL",
                "tool.name": tool_name,
                **{f"tool.arguments.{key}": value for key, value in arguments.items()},
            },
        ) as span:
            try:
                result: object
                if tool_name == "get_customer":
                    result = await tools.get_customer(arguments["customer_id"])
                elif tool_name == "get_order":
                    result = await tools.get_order(arguments["order_id"])
                elif tool_name == "get_refund_policy":
                    result = await tools.get_refund_policy(arguments["order_id"])
                elif tool_name == "calculate_refund":
                    result = await tools.calculate_refund(
                        arguments["order_id"], tuple(arguments["item_skus"].split(","))
                    )
                elif tool_name == "submit_refund":
                    approval_value = arguments["approval"]
                    approval = (
                        None if approval_value == "none" else Approval(approved_by=approval_value)
                    )
                    result = await tools.submit_refund(
                        customer_id=arguments["customer_id"],
                        order_id=arguments["order_id"],
                        amount=Decimal(arguments["amount"]),
                        item_skus=tuple(arguments["item_skus"].split(",")),
                        reason=arguments["reason"],
                        idempotency_key=arguments["idempotency_key"],
                        approval=approval,
                    )
                else:
                    raise KeyError(f"unknown tool: {tool_name}")
                span.set_status(Status(StatusCode.OK))
                observation = str(result)
                span.set_attribute("tool.result", observation)
            except (KeyError, RefundRejected) as error:
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR, str(error)))
                span.set_attribute("tool.error.type", type(error).__name__)
                span.set_attribute("tool.error.message", str(error))
                observation = f"ERROR:{type(error).__name__}:{error}"
                if arguments.get("ignore_error") != "true":
                    return {
                        "observations": [*state["observations"], observation],
                        "step": state["step"] + 1,
                        "outcome": RunOutcome.FAILED.value,
                    }
            return {
                "observations": [*state["observations"], observation],
                "step": state["step"] + 1,
                "next_tool": None,
                "next_arguments": {},
            }

    def after_decide(state: SupportState) -> str:
        return "end" if state["outcome"] is not None else "execute"

    def after_execute(state: SupportState) -> str:
        return "end" if state["outcome"] is not None else "decide"

    builder = StateGraph(SupportState)
    builder.add_node("decide", decide)
    builder.add_node("execute", execute)
    builder.set_entry_point("decide")
    builder.add_conditional_edges("decide", after_decide, {"execute": "execute", "end": END})
    builder.add_conditional_edges("execute", after_execute, {"decide": "decide", "end": END})
    graph = builder.compile()
    initial: SupportState = {
        "step": 0,
        "observations": [],
        "next_tool": None,
        "next_arguments": {},
        "final_message": None,
        "outcome": None,
    }
    with tracer.start_as_current_span(
        "supportlab.run",
        attributes={
            "openinference.span.kind": "AGENT",
            "scenario.id": scenario.scenario_id,
            "scenario.expected_failure": scenario.expected_failure.value,
        },
    ) as run_span:
        final = cast(SupportState, await graph.ainvoke(initial))
        run_span.set_attribute("run.outcome", final["outcome"] or RunOutcome.FAILED.value)
        if final["final_message"] is not None:
            run_span.set_attribute("run.final_message", final["final_message"])
    return SupportRunResult(
        scenario_id=scenario.scenario_id,
        outcome=RunOutcome(final["outcome"] or RunOutcome.FAILED.value),
        steps=final["step"],
        observations=tuple(final["observations"]),
        final_message=final["final_message"],
    )
