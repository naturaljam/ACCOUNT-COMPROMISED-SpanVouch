from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from typing import TypedDict, cast

from langgraph.graph import END, StateGraph
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import Status, StatusCode, Tracer
from pydantic import JsonValue

from spanvouch.labs.runtime import (
    AgentAction,
    ExecutionProvenance,
    ExecutionRecord,
    ExecutionStatus,
    FrameworkId,
    LabEnvironment,
    LabEnvironmentRegistry,
    LabScenario,
    RuntimeConfig,
    RuntimeFailure,
    RuntimeFailureCategory,
    RuntimeState,
    ToolObservation,
)
from spanvouch.observability.tracing import build_run_tracer
from spanvouch.trace.mapper import map_spans

_SERVICE_NAME = "spanvouch.labs.langgraph"
_TERMINAL_SUCCESS_MESSAGE = "Environment reported successful completion."


class _GraphState(TypedDict):
    step: int
    tool_calls: int
    observations: tuple[ToolObservation, ...]
    final_message: str | None
    failure: RuntimeFailure | None


@dataclass(frozen=True)
class _RunResult:
    state: RuntimeState
    status: ExecutionStatus
    failure: RuntimeFailure | None


class LangGraphRuntimeAdapter:
    framework_id = FrameworkId.LANGGRAPH

    def __init__(
        self,
        environment_registry: LabEnvironmentRegistry,
        *,
        provenance: ExecutionProvenance,
    ) -> None:
        self._environment_registry = environment_registry
        self._provenance = provenance
        self.framework_version = version("langgraph")

    async def execute(
        self,
        scenario: LabScenario,
        run_config: RuntimeConfig,
    ) -> ExecutionRecord:
        tracer, exporter = build_run_tracer(_SERVICE_NAME)
        started_at = datetime.now(UTC)
        finished_spans: tuple[ReadableSpan, ...] = ()
        try:
            result = await _run_langgraph_environment(
                scenario=scenario,
                run_config=run_config,
                environment_factory=lambda: self._environment_registry.build(scenario),
                tracer=tracer,
                timeout_seconds=run_config.timeout_seconds,
                emit_workflow_spans=True,
                map_cancellation=False,
                map_exceptions=True,
            )
        finally:
            finished_spans = tuple(exporter.get_finished_spans())
            exporter.clear()
        completed_at = datetime.now(UTC)
        trace = map_spans(scenario.scenario_id, _root_first(finished_spans))
        return ExecutionRecord.from_run(
            scenario=scenario,
            run_config=run_config,
            framework_id=self.framework_id,
            framework_version=self.framework_version,
            trace=trace,
            state=result.state,
            status=result.status,
            failure=result.failure,
            started_at=started_at,
            completed_at=completed_at,
            provenance=self._provenance,
        )


async def _run_langgraph_environment(
    *,
    scenario: LabScenario,
    run_config: RuntimeConfig,
    environment_factory: Callable[[], LabEnvironment],
    tracer: Tracer,
    timeout_seconds: float | None,
    emit_workflow_spans: bool,
    map_cancellation: bool,
    map_exceptions: bool,
) -> _RunResult:
    latest_state = RuntimeState.initial()
    result: _RunResult | None = None

    def remember_progress(state: RuntimeState) -> None:
        nonlocal latest_state
        latest_state = state

    with tracer.start_as_current_span(
        f"{scenario.domain}.run",
        attributes={
            "openinference.span.kind": "AGENT",
            "scenario.id": scenario.scenario_id,
        },
    ) as run_span:
        try:
            environment = environment_factory()
            invocation = _invoke_graph(
                environment=environment,
                initial_state=latest_state,
                run_config=run_config,
                tracer=tracer,
                emit_workflow_spans=emit_workflow_spans,
                on_progress=remember_progress,
            )
            if timeout_seconds is None:
                latest_state = await invocation
            else:
                async with asyncio.timeout(timeout_seconds):
                    latest_state = await invocation
            result = _result_from_state(environment, latest_state, run_config)
        except TimeoutError:
            failure = _failure(
                RuntimeFailureCategory.INFRASTRUCTURE,
                code="timeout",
                retryable=True,
            )
            latest_state = latest_state.with_failure(failure)
            result = _RunResult(latest_state, ExecutionStatus.FAILED, failure)
        except asyncio.CancelledError:
            if not map_cancellation:
                raise
            failure = _failure(
                RuntimeFailureCategory.INFRASTRUCTURE,
                code="cancelled",
                retryable=True,
            )
            latest_state = latest_state.with_failure(failure)
            result = _RunResult(latest_state, ExecutionStatus.FAILED, failure)
        except Exception as error:
            if not map_exceptions:
                raise
            failure = _exception_failure(error)
            latest_state = latest_state.with_failure(failure)
            status = (
                ExecutionStatus.INCOMPATIBLE
                if failure.category is RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY
                else ExecutionStatus.FAILED
            )
            result = _RunResult(latest_state, status, failure)
        finally:
            if result is not None:
                run_span.set_attribute("run.outcome", result.status.value)
                if result.state.final_message is not None:
                    run_span.set_attribute("run.final_message", result.state.final_message)
                if result.status is ExecutionStatus.SUCCEEDED:
                    run_span.set_status(Status(StatusCode.OK))
                else:
                    run_span.set_status(Status(StatusCode.ERROR, result.status.value))
    if result is None:
        raise RuntimeError("LangGraph execution ended without a result")
    return result


async def _invoke_graph(
    *,
    environment: LabEnvironment,
    initial_state: RuntimeState,
    run_config: RuntimeConfig,
    tracer: Tracer,
    emit_workflow_spans: bool,
    on_progress: Callable[[RuntimeState], None],
) -> RuntimeState:
    pending_action: AgentAction | None = None
    latest_state = _apply_terminal_or_limit(environment, initial_state, run_config)
    on_progress(latest_state)

    async def decide(state: _GraphState) -> _GraphState:
        nonlocal latest_state, pending_action
        runtime_state = _apply_terminal_or_limit(
            environment, _runtime_state(state), run_config
        )
        if runtime_state.final_message is not None or runtime_state.failure is not None:
            latest_state = runtime_state
            pending_action = None
            on_progress(latest_state)
            return _graph_state(latest_state)
        if emit_workflow_spans:
            with tracer.start_as_current_span(
                f"{environment.scenario.domain}.decision",
                attributes={"openinference.span.kind": "CHAIN"},
            ) as span:
                action = await environment.decide(runtime_state)
                span.set_status(Status(StatusCode.OK))
        else:
            action = await environment.decide(runtime_state)
        runtime_state = _apply_terminal_or_limit(environment, runtime_state, run_config)
        if runtime_state.final_message is not None or runtime_state.failure is not None:
            latest_state = runtime_state
            pending_action = None
        elif action.kind == "final":
            latest_state = runtime_state.with_final(action.final_message or "")
            pending_action = None
        else:
            pending_action = action
            latest_state = runtime_state
        on_progress(latest_state)
        return _graph_state(latest_state)

    async def execute(state: _GraphState) -> _GraphState:
        nonlocal latest_state, pending_action
        runtime_state = _runtime_state(state)
        action = pending_action
        if action is None or action.tool_name is None:
            raise RuntimeError("execute node scheduled without a tool action")
        observation = await _execute_tool(environment, action, tracer)
        latest_state = runtime_state.with_observation(observation)
        pending_action = None
        latest_state = _apply_terminal_or_limit(environment, latest_state, run_config)
        on_progress(latest_state)
        return _graph_state(latest_state)

    def after_decide(state: _GraphState) -> str:
        runtime_state = _runtime_state(state)
        if runtime_state.final_message is not None or runtime_state.failure is not None:
            return "end"
        return "execute"

    def after_execute(state: _GraphState) -> str:
        runtime_state = _runtime_state(state)
        if runtime_state.final_message is not None or runtime_state.failure is not None:
            return "end"
        return "decide"

    def before_start(state: _GraphState) -> str:
        runtime_state = _runtime_state(state)
        if runtime_state.final_message is not None or runtime_state.failure is not None:
            return "end"
        return "decide"

    builder = StateGraph(_GraphState)
    builder.add_node("decide", decide)
    builder.add_node("execute", execute)
    builder.set_conditional_entry_point(before_start, {"decide": "decide", "end": END})
    builder.add_conditional_edges("decide", after_decide, {"execute": "execute", "end": END})
    builder.add_conditional_edges("execute", after_execute, {"decide": "decide", "end": END})
    graph = builder.compile()
    recursion_limit = max(25, 2 * max(run_config.max_steps, run_config.max_tool_calls) + 3)
    final = await graph.ainvoke(
        _graph_state(latest_state),
        config={"recursion_limit": recursion_limit},
    )
    return _runtime_state(cast(_GraphState, final))


async def _execute_tool(
    environment: LabEnvironment,
    action: AgentAction,
    tracer: Tracer,
) -> ToolObservation:
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
            exception_type = cast(str, error["exception_type"])
            span.add_event(
                "exception",
                attributes={
                    "exception.type": exception_type,
                    "exception.message": error_message,
                    "exception.escaped": "False",
                },
            )
            span.set_status(Status(StatusCode.ERROR, error_message))
            span.set_attribute("tool.error.type", error_type)
            span.set_attribute("tool.error.message", error_message)
    return observation


def _apply_terminal_or_limit(
    environment: LabEnvironment,
    state: RuntimeState,
    run_config: RuntimeConfig,
) -> RuntimeState:
    if state.final_message is not None or state.failure is not None:
        return state
    terminal = environment.terminal_status(state)
    if terminal is ExecutionStatus.SUCCEEDED:
        return state.with_final(_TERMINAL_SUCCESS_MESSAGE)
    if terminal is ExecutionStatus.INCOMPATIBLE:
        if state.failure is not None:
            return state
        return state.with_failure(
            _failure(
                RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY,
                code="environment_incompatible",
                retryable=False,
            )
        )
    if terminal is ExecutionStatus.FAILED:
        if state.failure is not None:
            return state
        return state.with_failure(
            _failure(
                RuntimeFailureCategory.FRAMEWORK_EXECUTION,
                code="tool_error" if _last_observation_failed(state) else "terminal_failure",
                retryable=False,
            )
        )
    # RuntimeConfig is the cross-framework budget authority. Some domain
    # environments retain a legacy STEP_LIMIT cap, so only these checks may
    # terminate a framework run for budget exhaustion.
    if state.step >= run_config.max_steps:
        return state.with_failure(
            _failure(
                RuntimeFailureCategory.FRAMEWORK_EXECUTION,
                code="step_limit",
                retryable=False,
            )
        )
    if state.tool_calls >= run_config.max_tool_calls:
        return state.with_failure(
            _failure(
                RuntimeFailureCategory.FRAMEWORK_EXECUTION,
                code="tool_call_limit",
                retryable=False,
            )
        )
    return state


def _result_from_state(
    environment: LabEnvironment,
    state: RuntimeState,
    run_config: RuntimeConfig,
) -> _RunResult:
    state = _apply_terminal_or_limit(environment, state, run_config)
    terminal = environment.terminal_status(state)
    if state.final_message is not None and terminal is ExecutionStatus.SUCCEEDED:
        return _RunResult(state, ExecutionStatus.SUCCEEDED, None)
    if state.failure is None:
        failure = _failure(
            RuntimeFailureCategory.FRAMEWORK_EXECUTION,
            code="non_terminal_result",
            retryable=False,
        )
        state = state.with_failure(failure)
        return _RunResult(state, ExecutionStatus.FAILED, failure)
    if state.failure.category is RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY:
        return _RunResult(state, ExecutionStatus.INCOMPATIBLE, state.failure)
    status = (
        ExecutionStatus.STEP_LIMIT
        if state.failure.code in {"step_limit", "tool_call_limit"}
        else ExecutionStatus.FAILED
    )
    return _RunResult(state, status, state.failure)


def _exception_failure(error: Exception) -> RuntimeFailure:
    candidate = getattr(error, "failure", None)
    if isinstance(candidate, RuntimeFailure):
        return candidate
    return _failure(
        RuntimeFailureCategory.FRAMEWORK_EXECUTION,
        code="framework_exception",
        retryable=False,
    )


def _failure(
    category: RuntimeFailureCategory,
    *,
    code: str,
    retryable: bool,
) -> RuntimeFailure:
    return RuntimeFailure.from_message(
        category=category,
        code=code,
        retryable=retryable,
        sanitized_message=code,
    )


def _last_observation_failed(state: RuntimeState) -> bool:
    return bool(state.observations and state.observations[-1].status == "error")


def _graph_state(state: RuntimeState) -> _GraphState:
    return {
        "step": state.step,
        "tool_calls": state.tool_calls,
        "observations": state.observations,
        "final_message": state.final_message,
        "failure": state.failure,
    }


def _runtime_state(state: _GraphState) -> RuntimeState:
    return RuntimeState.model_validate(state)


def _trace_attribute(value: JsonValue) -> str | bool | int | float:
    if isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _root_first(spans: Sequence[ReadableSpan]) -> tuple[ReadableSpan, ...]:
    return tuple(
        sorted(
            spans,
            key=lambda span: (
                span.parent is not None,
                span.start_time if span.start_time is not None else 0,
            ),
        )
    )
