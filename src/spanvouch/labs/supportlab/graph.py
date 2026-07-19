from enum import StrEnum
from typing import Any, TypedDict, cast

from langgraph.graph import END, StateGraph
from opentelemetry.trace import Status, StatusCode, Tracer
from pydantic import BaseModel, ConfigDict, JsonValue

from spanvouch.labs.runtime import AgentAction, ExecutionStatus, RuntimeState, ToolObservation
from spanvouch.labs.supportlab.decision import DecisionModel
from spanvouch.labs.supportlab.environment import SupportLabEnvironment
from spanvouch.labs.supportlab.runtime import support_scenario_to_lab
from spanvouch.labs.supportlab.scenarios import Scenario
from spanvouch.labs.supportlab.tools import SupportTools


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
    runtime_state: RuntimeState
    next_action: AgentAction | None
    outcome: str | None


async def run_support_scenario(
    *,
    scenario: Scenario,
    tools: SupportTools,
    decision_model: DecisionModel,
    tracer: Tracer,
    max_steps: int = 8,
) -> SupportRunResult:
    environment = SupportLabEnvironment(
        scenario=support_scenario_to_lab(scenario),
        tools=tools,
        decision_model=decision_model,
        max_steps=max_steps,
    )
    return await _run_support_environment_legacy_langgraph(
        historical_scenario=scenario,
        environment=environment,
        tracer=tracer,
    )


async def _run_support_environment_legacy_langgraph(
    *,
    historical_scenario: Scenario,
    environment: SupportLabEnvironment,
    tracer: Tracer,
) -> SupportRunResult:
    async def decide(state: SupportState) -> dict[str, Any]:
        terminal = environment.terminal_status(state["runtime_state"])
        if terminal is not None:
            return {"outcome": _run_outcome(terminal).value}
        action = await environment.decide(state["runtime_state"])
        if action.kind == "final":
            runtime_state = state["runtime_state"].with_final(action.final_message or "")
            return {
                "runtime_state": runtime_state,
                "outcome": RunOutcome.SUCCEEDED.value,
            }
        return {"next_action": action}

    async def execute(state: SupportState) -> dict[str, Any]:
        action = state["next_action"]
        assert action is not None
        assert action.tool_name is not None
        with tracer.start_as_current_span(
            action.tool_name,
            attributes={
                "openinference.span.kind": "TOOL",
                "tool.name": action.tool_name,
                **{
                    f"tool.arguments.{key}": _trace_attribute(value)
                    for key, value in action.arguments.items()
                },
            },
        ) as span:
            observation = await environment.execute(action)
            if observation.status == "ok":
                span.set_status(Status(StatusCode.OK))
                span.set_attribute("tool.result", cast(str, observation.result))
            else:
                error = cast(dict[str, JsonValue], observation.error)
                error_type = cast(str, error["type"])
                error_message = cast(str, error["message"])
                span.record_exception(RuntimeError(error_message))
                span.set_status(Status(StatusCode.ERROR, error_message))
                span.set_attribute("tool.error.type", error_type)
                span.set_attribute("tool.error.message", error_message)
        runtime_state = state["runtime_state"].with_observation(observation)
        terminal = environment.terminal_status(runtime_state)
        return {
            "runtime_state": runtime_state,
            "next_action": None,
            "outcome": _run_outcome(terminal).value if terminal is not None else None,
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
        "runtime_state": RuntimeState.initial(),
        "next_action": None,
        "outcome": None,
    }
    with tracer.start_as_current_span(
        "supportlab.run",
        attributes={
            "openinference.span.kind": "AGENT",
            "scenario.id": historical_scenario.scenario_id,
            "scenario.expected_failure": historical_scenario.expected_failure.value,
        },
    ) as run_span:
        final = cast(SupportState, await graph.ainvoke(initial))
        outcome = RunOutcome(final["outcome"] or RunOutcome.FAILED.value)
        run_span.set_attribute("run.outcome", outcome.value)
        if outcome is RunOutcome.SUCCEEDED:
            run_span.set_status(Status(StatusCode.OK))
        else:
            run_span.set_status(Status(StatusCode.ERROR, outcome.value))
        final_message = final["runtime_state"].final_message
        if final_message is not None:
            run_span.set_attribute("run.final_message", final_message)
    return SupportRunResult(
        scenario_id=historical_scenario.scenario_id,
        outcome=outcome,
        steps=final["runtime_state"].step,
        observations=tuple(
            _legacy_observation_text(item)
            for item in final["runtime_state"].observations
        ),
        final_message=final["runtime_state"].final_message,
    )


def _run_outcome(status: ExecutionStatus) -> RunOutcome:
    if status is ExecutionStatus.SUCCEEDED:
        return RunOutcome.SUCCEEDED
    if status is ExecutionStatus.STEP_LIMIT:
        return RunOutcome.STEP_LIMIT
    return RunOutcome.FAILED


def _legacy_observation_text(observation: ToolObservation) -> str:
    if observation.status == "ok":
        return cast(str, observation.result)
    error = cast(dict[str, JsonValue], observation.error)
    return f"ERROR:{error['type']}:{error['message']}"


def _trace_attribute(value: JsonValue) -> str | bool | int | float:
    if isinstance(value, (str, bool, int, float)):
        return value
    return str(value)
