from enum import StrEnum
from typing import cast

from opentelemetry.trace import Tracer
from pydantic import BaseModel, ConfigDict, JsonValue

from spanvouch.labs.frameworks.langgraph import _run_langgraph_environment
from spanvouch.labs.runtime import ExecutionStatus, RuntimeConfig, ToolObservation
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


async def run_support_scenario(
    *,
    scenario: Scenario,
    tools: SupportTools,
    decision_model: DecisionModel,
    tracer: Tracer,
    max_steps: int = 8,
) -> SupportRunResult:
    lab_scenario = support_scenario_to_lab(scenario)
    environment = SupportLabEnvironment(
        scenario=lab_scenario,
        tools=tools,
        decision_model=decision_model,
        max_steps=max_steps,
    )
    result = await _run_langgraph_environment(
        scenario=lab_scenario,
        run_config=RuntimeConfig(
            seed=0,
            repetition=1,
            max_steps=max_steps,
            timeout_seconds=1.0,
            max_retries=0,
            max_tool_calls=max_steps,
        ),
        environment_factory=lambda: environment,
        tracer=tracer,
        timeout_seconds=None,
        emit_workflow_spans=False,
        map_cancellation=False,
    )
    return SupportRunResult(
        scenario_id=scenario.scenario_id,
        outcome=_run_outcome(result.status),
        steps=result.state.step,
        observations=tuple(
            _legacy_observation_text(item) for item in result.state.observations
        ),
        final_message=result.state.final_message,
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
