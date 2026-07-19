from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import version
from typing import cast

from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.base import Response, TaskResult
from autogen_agentchat.conditions import FunctionalTermination, MaxMessageTermination
from autogen_agentchat.messages import BaseAgentEvent, BaseChatMessage, TextMessage
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_core import CancellationToken
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import Status, StatusCode, Tracer
from pydantic import JsonValue, ValidationError

from spanvouch.contracts.versioning import canonical_json
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

_SERVICE_NAME = "spanvouch.labs.autogen"
_TERMINAL_SUCCESS_MESSAGE = "Environment reported successful completion."


@dataclass
class AutoGenLabSession:
    environment: LabEnvironment
    run_config: RuntimeConfig
    tracer: Tracer
    state: RuntimeState = field(default_factory=RuntimeState.initial)


@dataclass(frozen=True)
class _RunResult:
    state: RuntimeState
    status: ExecutionStatus
    failure: RuntimeFailure | None


class EnvironmentActionAgent(BaseChatAgent):
    def __init__(self, session: AutoGenLabSession) -> None:
        super().__init__(
            name="spanvouch_lab_agent",
            description="Executes one lab decision",
        )
        self._session = session

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

    async def on_messages(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> Response:
        del messages
        _raise_if_cancelled(cancellation_token)
        self._session.state = _apply_terminal_or_limit(
            self._session.environment,
            self._session.state,
            self._session.run_config,
        )
        if _is_terminal(self._session.state):
            raise RuntimeError("decision agent scheduled after terminal state")
        with self._session.tracer.start_as_current_span(
            f"{self._session.environment.scenario.domain}.decision",
            attributes={"openinference.span.kind": "CHAIN"},
        ) as span:
            action = await _await_with_cancellation(
                self._session.environment.decide(self._session.state),
                cancellation_token,
            )
            span.set_status(Status(StatusCode.OK))
        _raise_if_cancelled(cancellation_token)
        self._session.state = _apply_terminal_or_limit(
            self._session.environment,
            self._session.state,
            self._session.run_config,
        )
        if not _is_terminal(self._session.state) and action.kind == "final":
            self._session.state = self._session.state.with_final(
                action.final_message or ""
            )
        return Response(
            chat_message=TextMessage(
                content=canonical_json(action),
                source=self.name,
            )
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        _raise_if_cancelled(cancellation_token)
        self._session.state = RuntimeState.initial()


class EnvironmentToolAgent(BaseChatAgent):
    def __init__(self, session: AutoGenLabSession) -> None:
        super().__init__(
            name="spanvouch_lab_tool",
            description="Executes one deterministic lab tool action",
        )
        self._session = session

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

    async def on_messages(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> Response:
        _raise_if_cancelled(cancellation_token)
        action = _latest_action(messages)
        if action.kind != "tool":
            raise RuntimeError("final action must not be sent to the tool agent")
        observation = await _await_with_cancellation(
            _execute_tool(
                self._session.environment,
                action,
                self._session.tracer,
            ),
            cancellation_token,
        )
        _raise_if_cancelled(cancellation_token)
        self._session.state = self._session.state.with_observation(observation)
        self._session.state = _apply_terminal_or_limit(
            self._session.environment,
            self._session.state,
            self._session.run_config,
        )
        return Response(
            chat_message=TextMessage(
                content=canonical_json(observation),
                source=self.name,
            )
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        _raise_if_cancelled(cancellation_token)


class AutoGenRuntimeAdapter:
    framework_id = FrameworkId.AUTOGEN

    def __init__(
        self,
        environment_registry: LabEnvironmentRegistry,
        *,
        provenance: ExecutionProvenance,
    ) -> None:
        self._environment_registry = environment_registry
        self._provenance = provenance
        self.framework_version = version("autogen-agentchat")

    async def execute(
        self,
        scenario: LabScenario,
        run_config: RuntimeConfig,
    ) -> ExecutionRecord:
        tracer, exporter = build_run_tracer(_SERVICE_NAME)
        started_at = datetime.now(UTC)
        finished_spans: tuple[ReadableSpan, ...] = ()
        try:
            result = await _run_autogen_environment(
                scenario=scenario,
                run_config=run_config,
                environment_registry=self._environment_registry,
                tracer=tracer,
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


async def _run_autogen_environment(
    *,
    scenario: LabScenario,
    run_config: RuntimeConfig,
    environment_registry: LabEnvironmentRegistry,
    tracer: Tracer,
) -> _RunResult:
    session: AutoGenLabSession | None = None
    action_agent: EnvironmentActionAgent | None = None
    tool_agent: EnvironmentToolAgent | None = None
    team: RoundRobinGroupChat | None = None
    team_task: asyncio.Task[TaskResult] | None = None
    token = CancellationToken()
    result: _RunResult | None = None

    with tracer.start_as_current_span(
        f"{scenario.domain}.run",
        attributes={
            "openinference.span.kind": "AGENT",
            "scenario.id": scenario.scenario_id,
        },
    ) as run_span:
        try:
            environment = environment_registry.build(scenario)
            session = AutoGenLabSession(
                environment=environment,
                run_config=run_config,
                tracer=tracer,
            )
            action_agent = EnvironmentActionAgent(session)
            tool_agent = EnvironmentToolAgent(session)
            functional = FunctionalTermination(
                lambda messages: _should_terminate(session, messages)
            )
            message_limit = MaxMessageTermination(2 * run_config.max_steps + 1)
            team = RoundRobinGroupChat(
                [action_agent, tool_agent],
                termination_condition=functional | message_limit,
            )
            team_task = asyncio.create_task(
                team.run(
                    task=TextMessage(content=scenario.user_request, source="user"),
                    cancellation_token=token,
                )
            )
            async with asyncio.timeout(run_config.timeout_seconds):
                await asyncio.shield(team_task)
            result = _result_from_state(
                environment,
                session.state,
                run_config,
            )
        except TimeoutError:
            await _cancel_team_run(team_task, token)
            state = session.state if session is not None else RuntimeState.initial()
            failure = _failure(
                RuntimeFailureCategory.INFRASTRUCTURE,
                code="timeout",
                retryable=True,
            )
            state = _replace_terminal_with_failure(state, failure)
            result = _RunResult(state, ExecutionStatus.FAILED, failure)
        except asyncio.CancelledError:
            await _cancel_team_run(team_task, token)
            raise
        except Exception as error:
            failure = _exception_failure(error)
            state = session.state if session is not None else RuntimeState.initial()
            state = _replace_terminal_with_failure(state, failure)
            status = (
                ExecutionStatus.INCOMPATIBLE
                if failure.category
                is RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY
                else ExecutionStatus.FAILED
            )
            result = _RunResult(state, status, failure)
        finally:
            if result is not None:
                run_span.set_attribute("run.outcome", result.status.value)
                if result.state.final_message is not None:
                    run_span.set_attribute(
                        "run.final_message",
                        result.state.final_message,
                    )
                if result.status is ExecutionStatus.SUCCEEDED:
                    run_span.set_status(Status(StatusCode.OK))
                else:
                    run_span.set_status(
                        Status(StatusCode.ERROR, result.status.value)
                    )
            await _cleanup_team(team, action_agent, tool_agent)
    if result is None:
        raise RuntimeError("AutoGen execution ended without a result")
    return result


def _should_terminate(
    session: AutoGenLabSession,
    messages: Sequence[BaseAgentEvent | BaseChatMessage],
) -> bool:
    if messages:
        latest = messages[-1]
        if isinstance(latest, TextMessage) and latest.source == "spanvouch_lab_agent":
            try:
                action = AgentAction.model_validate_json(latest.content)
            except ValidationError:
                pass
            else:
                if action.kind == "final":
                    return True
    session.state = _apply_terminal_or_limit(
        session.environment,
        session.state,
        session.run_config,
    )
    return _is_terminal(session.state)


async def _cleanup_team(
    team: RoundRobinGroupChat | None,
    action_agent: EnvironmentActionAgent | None,
    tool_agent: EnvironmentToolAgent | None,
) -> None:
    if team is not None:
        with suppress(RuntimeError):
            await team.reset()
    if action_agent is not None:
        await action_agent.close()
    if tool_agent is not None:
        await tool_agent.close()


async def _cancel_team_run(
    team_task: asyncio.Task[TaskResult] | None,
    token: CancellationToken,
) -> None:
    token.cancel()
    if team_task is None:
        return
    if not team_task.done():
        team_task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await team_task


async def _await_with_cancellation[T](
    awaitable: Awaitable[T],
    cancellation_token: CancellationToken,
) -> T:
    future = asyncio.ensure_future(awaitable)
    cancellation_token.link_future(future)
    return await future


def _latest_action(messages: Sequence[BaseChatMessage]) -> AgentAction:
    if not messages or not isinstance(messages[-1], TextMessage):
        raise RuntimeError("tool agent requires a TextMessage action")
    try:
        return AgentAction.model_validate_json(messages[-1].content)
    except ValidationError as error:
        raise RuntimeError("tool agent received an invalid action") from error


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
    if _is_terminal(state):
        return state
    terminal = environment.terminal_status(state)
    if terminal is ExecutionStatus.SUCCEEDED:
        return state.with_final(_TERMINAL_SUCCESS_MESSAGE)
    if terminal is ExecutionStatus.INCOMPATIBLE:
        return state.with_failure(
            _failure(
                RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY,
                code="environment_incompatible",
                retryable=False,
            )
        )
    if terminal is ExecutionStatus.FAILED:
        return state.with_failure(
            _failure(
                RuntimeFailureCategory.FRAMEWORK_EXECUTION,
                code=(
                    "tool_error" if _last_observation_failed(state) else "terminal_failure"
                ),
                retryable=False,
            )
        )
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


def _replace_terminal_with_failure(
    state: RuntimeState,
    failure: RuntimeFailure,
) -> RuntimeState:
    if state.failure is not None:
        state = state.model_copy(update={"failure": None})
    if state.final_message is not None:
        state = state.model_copy(update={"final_message": None})
    return state.with_failure(failure)


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


def _is_terminal(state: RuntimeState) -> bool:
    return state.final_message is not None or state.failure is not None


def _last_observation_failed(state: RuntimeState) -> bool:
    return bool(state.observations and state.observations[-1].status == "error")


def _raise_if_cancelled(cancellation_token: CancellationToken) -> None:
    if cancellation_token.is_cancelled():
        raise asyncio.CancelledError


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
