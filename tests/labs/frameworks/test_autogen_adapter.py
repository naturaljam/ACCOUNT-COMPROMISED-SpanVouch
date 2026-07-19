import asyncio
from collections.abc import Callable
from importlib.metadata import version
from unittest.mock import patch

import pytest
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from spanvouch.contracts.trace import SpanKind
from spanvouch.contracts.versioning import canonical_json, canonical_sha256
from spanvouch.labs.frameworks import autogen as autogen_runtime
from spanvouch.labs.frameworks import langgraph as langgraph_runtime
from spanvouch.labs.frameworks.autogen import (
    AutoGenLabSession,
    AutoGenRuntimeAdapter,
    EnvironmentActionAgent,
    EnvironmentToolAgent,
)
from spanvouch.labs.frameworks.langgraph import LangGraphRuntimeAdapter
from spanvouch.labs.runtime import (
    AgentAction,
    ExecutionProvenance,
    ExecutionStatus,
    FrameworkId,
    LabEnvironment,
    LabScenario,
    RuntimeConfig,
    RuntimeFailure,
    RuntimeFailureCategory,
    RuntimeState,
    ToolObservation,
)
from spanvouch.labs.supportlab.environment import SupportLabEnvironmentRegistry
from spanvouch.labs.supportlab.runtime import support_scenario_to_lab
from spanvouch.labs.supportlab.scenarios import build_scenarios
from spanvouch.observability.tracing import build_run_tracer


@pytest.fixture
def execution_provenance() -> ExecutionProvenance:
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


def _scenario(scenario_id: str) -> LabScenario:
    return support_scenario_to_lab(
        next(item for item in build_scenarios() if item.scenario_id == scenario_id)
    )


def _config(**overrides: int | float) -> RuntimeConfig:
    values: dict[str, int | float] = {
        "seed": 20260719,
        "repetition": 1,
        "max_steps": 8,
        "timeout_seconds": 5.0,
        "max_retries": 0,
        "max_tool_calls": 8,
    }
    values.update(overrides)
    return RuntimeConfig.model_validate(values)


@pytest.mark.asyncio
async def test_autogen_adapter_returns_a_hashed_contract_valid_record_without_network(
    execution_provenance: ExecutionProvenance,
) -> None:
    adapter = AutoGenRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    )

    with patch(
        "socket.socket.connect",
        side_effect=AssertionError("Stage A must not call a provider"),
    ):
        record = await adapter.execute(_scenario("clean-01"), _config())

    assert record.framework_id is FrameworkId.AUTOGEN
    assert record.framework_version == version("autogen-agentchat")
    assert record.status is ExecutionStatus.SUCCEEDED
    assert record.tool_calls == 5
    assert record.trace_sha256 == canonical_sha256(record.trace)
    roots = tuple(span for span in record.trace.spans if span.kind is SpanKind.AGENT)
    assert len(roots) == 1
    assert roots[0].name == "supportlab.run"
    assert roots[0].parent_span_id is None


@pytest.mark.asyncio
async def test_autogen_step_limit_maps_to_one_framework_execution_failure(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await AutoGenRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    ).execute(_scenario("loop_or_budget_exhaustion-01"), _config(max_steps=2))

    assert record.status is ExecutionStatus.STEP_LIMIT
    assert record.steps == 2
    assert record.failure is not None
    assert record.failure.category is RuntimeFailureCategory.FRAMEWORK_EXECUTION
    assert record.failure.code == "step_limit"


@pytest.mark.asyncio
async def test_autogen_run_config_step_limit_overrides_environment_default(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await AutoGenRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    ).execute(
        _scenario("loop_or_budget_exhaustion-01"),
        _config(max_steps=9, max_tool_calls=10),
    )

    assert record.status is ExecutionStatus.STEP_LIMIT
    assert record.steps == 9
    assert record.tool_calls == 9
    assert record.failure is not None
    assert record.failure.code == "step_limit"


@pytest.mark.asyncio
async def test_autogen_tool_call_limit_maps_to_one_framework_execution_failure(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await AutoGenRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    ).execute(_scenario("clean-01"), _config(max_tool_calls=2))

    assert record.status is ExecutionStatus.STEP_LIMIT
    assert record.tool_calls == 2
    assert record.failure is not None
    assert record.failure.category is RuntimeFailureCategory.FRAMEWORK_EXECUTION
    assert record.failure.code == "tool_call_limit"


@pytest.mark.asyncio
async def test_autogen_tool_fault_maps_to_one_framework_execution_failure(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await AutoGenRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    ).execute(_scenario("wrong_tool-01"), _config())

    assert record.status is ExecutionStatus.FAILED
    assert record.failure is not None
    assert record.failure.category is RuntimeFailureCategory.FRAMEWORK_EXECUTION
    assert record.failure.code == "tool_error"


@pytest.mark.asyncio
async def test_autogen_ignored_tool_error_remains_successful(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await AutoGenRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    ).execute(_scenario("ignored_tool_error-01"), _config())

    assert record.status is ExecutionStatus.SUCCEEDED
    assert record.failure is None
    assert record.tool_calls == 5


@pytest.mark.asyncio
async def test_autogen_tool_failure_at_the_limit_precedes_budget_failure(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await AutoGenRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    ).execute(
        _scenario("wrong_tool-01"),
        _config(max_steps=1, max_tool_calls=1),
    )

    assert record.status is ExecutionStatus.FAILED
    assert record.failure is not None
    assert record.failure.code == "tool_error"


@pytest.mark.asyncio
async def test_autogen_incompatibility_maps_to_one_typed_failure(
    execution_provenance: ExecutionProvenance,
) -> None:
    scenario = _scenario("clean-01").model_copy(update={"domain": "opslab"})

    record = await AutoGenRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    ).execute(scenario, _config())

    assert record.status is ExecutionStatus.INCOMPATIBLE
    assert record.failure is not None
    assert record.failure.category is RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY
    assert record.failure.code == "unsupported_domain"


class _BlockingEnvironment:
    def __init__(self, scenario: LabScenario, started: asyncio.Event) -> None:
        self.scenario = scenario
        self._started = started

    async def decide(self, state: RuntimeState) -> AgentAction:
        self._started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def execute(self, action: AgentAction) -> ToolObservation:
        raise AssertionError("unreachable")

    def terminal_status(self, state: RuntimeState) -> ExecutionStatus | None:
        return None


class _BlockingRegistry:
    def __init__(self, started: asyncio.Event) -> None:
        self._started = started

    def build(self, scenario: LabScenario) -> _BlockingEnvironment:
        return _BlockingEnvironment(scenario, self._started)


class _PartiallyBlockingEnvironment:
    def __init__(self, scenario: LabScenario) -> None:
        self.scenario = scenario

    async def decide(self, state: RuntimeState) -> AgentAction:
        if state.step == 0:
            return AgentAction(kind="tool", tool_name="probe")
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def execute(self, action: AgentAction) -> ToolObservation:
        return ToolObservation(
            tool_name="probe",
            result="observed",
            status="ok",
            retryable=False,
        )

    def terminal_status(self, state: RuntimeState) -> ExecutionStatus | None:
        return None


class _PartiallyBlockingRegistry:
    def build(self, scenario: LabScenario) -> _PartiallyBlockingEnvironment:
        return _PartiallyBlockingEnvironment(scenario)


@pytest.mark.asyncio
async def test_autogen_timeout_maps_to_one_infrastructure_failure_and_keeps_progress(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await AutoGenRuntimeAdapter(
        _PartiallyBlockingRegistry(), provenance=execution_provenance
    ).execute(_scenario("clean-01"), _config(timeout_seconds=0.1))

    assert record.status is ExecutionStatus.FAILED
    assert record.steps == 1
    assert record.tool_calls == 1
    assert record.failure is not None
    assert record.failure.category is RuntimeFailureCategory.INFRASTRUCTURE
    assert record.failure.code == "timeout"


@pytest.mark.asyncio
async def test_autogen_cancellation_propagates(
    execution_provenance: ExecutionProvenance,
) -> None:
    started = asyncio.Event()
    adapter = AutoGenRuntimeAdapter(
        _BlockingRegistry(started), provenance=execution_provenance
    )
    task = asyncio.create_task(adapter.execute(_scenario("clean-01"), _config()))
    await started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class _InitiallyTerminalEnvironment:
    def __init__(self, scenario: LabScenario) -> None:
        self.scenario = scenario
        self.decide_calls = 0

    async def decide(self, state: RuntimeState) -> AgentAction:
        self.decide_calls += 1
        raise AssertionError("terminal state must not schedule decide")

    async def execute(self, action: AgentAction) -> ToolObservation:
        raise AssertionError("terminal state must not schedule execute")

    def terminal_status(self, state: RuntimeState) -> ExecutionStatus | None:
        return ExecutionStatus.FAILED


class _InitiallyTerminalRegistry:
    def __init__(self) -> None:
        self.environment: _InitiallyTerminalEnvironment | None = None

    def build(self, scenario: LabScenario) -> _InitiallyTerminalEnvironment:
        self.environment = _InitiallyTerminalEnvironment(scenario)
        return self.environment


class _InitiallySuccessfulEnvironment:
    def __init__(self, scenario: LabScenario) -> None:
        self.scenario = scenario
        self.decide_calls = 0

    async def decide(self, state: RuntimeState) -> AgentAction:
        self.decide_calls += 1
        raise AssertionError("successful terminal state must not schedule decide")

    async def execute(self, action: AgentAction) -> ToolObservation:
        raise AssertionError("terminal state must not schedule execute")

    def terminal_status(self, state: RuntimeState) -> ExecutionStatus | None:
        return ExecutionStatus.SUCCEEDED


class _InitiallySuccessfulRegistry:
    def __init__(self) -> None:
        self.environment: _InitiallySuccessfulEnvironment | None = None

    def build(self, scenario: LabScenario) -> _InitiallySuccessfulEnvironment:
        self.environment = _InitiallySuccessfulEnvironment(scenario)
        return self.environment


class _InitiallyIncompatibleEnvironment:
    def __init__(self, scenario: LabScenario) -> None:
        self.scenario = scenario

    async def decide(self, state: RuntimeState) -> AgentAction:
        raise AssertionError("incompatible state must not schedule decide")

    async def execute(self, action: AgentAction) -> ToolObservation:
        raise AssertionError("incompatible state must not schedule execute")

    def terminal_status(self, state: RuntimeState) -> ExecutionStatus | None:
        return ExecutionStatus.INCOMPATIBLE


class _InitiallyIncompatibleRegistry:
    def build(self, scenario: LabScenario) -> _InitiallyIncompatibleEnvironment:
        return _InitiallyIncompatibleEnvironment(scenario)


class _ExplodingRegistry:
    def build(self, scenario: LabScenario) -> LabEnvironment:
        raise RuntimeError("unsanitized implementation detail")


class _TerminalAfterDecisionEnvironment:
    def __init__(self, scenario: LabScenario) -> None:
        self.scenario = scenario
        self.terminal = False
        self.execute_calls = 0

    async def decide(self, state: RuntimeState) -> AgentAction:
        self.terminal = True
        return AgentAction(kind="tool", tool_name="must_not_run")

    async def execute(self, action: AgentAction) -> ToolObservation:
        self.execute_calls += 1
        raise AssertionError("terminal transition must not schedule execute")

    def terminal_status(self, state: RuntimeState) -> ExecutionStatus | None:
        return ExecutionStatus.FAILED if self.terminal else None


class _TerminalAfterDecisionRegistry:
    def __init__(self) -> None:
        self.environment: _TerminalAfterDecisionEnvironment | None = None

    def build(self, scenario: LabScenario) -> _TerminalAfterDecisionEnvironment:
        self.environment = _TerminalAfterDecisionEnvironment(scenario)
        return self.environment


class _SuccessfulToolAtLimitEnvironment:
    def __init__(self, scenario: LabScenario) -> None:
        self.scenario = scenario

    async def decide(self, state: RuntimeState) -> AgentAction:
        return AgentAction(kind="tool", tool_name="finish")

    async def execute(self, action: AgentAction) -> ToolObservation:
        return ToolObservation(
            tool_name="finish",
            result="completed",
            status="ok",
            retryable=False,
        )

    def terminal_status(self, state: RuntimeState) -> ExecutionStatus | None:
        return ExecutionStatus.SUCCEEDED if state.tool_calls == 1 else None


class _SuccessfulToolAtLimitRegistry:
    def build(self, scenario: LabScenario) -> _SuccessfulToolAtLimitEnvironment:
        return _SuccessfulToolAtLimitEnvironment(scenario)


@pytest.mark.asyncio
async def test_autogen_initial_terminal_state_stops_before_decision(
    execution_provenance: ExecutionProvenance,
) -> None:
    registry = _InitiallyTerminalRegistry()

    record = await AutoGenRuntimeAdapter(
        registry, provenance=execution_provenance
    ).execute(_scenario("clean-01"), _config())

    assert record.status is ExecutionStatus.FAILED
    assert record.failure is not None
    assert record.failure.code == "terminal_failure"
    assert registry.environment is not None
    assert registry.environment.decide_calls == 0


@pytest.mark.asyncio
async def test_autogen_initial_success_stops_before_decision(
    execution_provenance: ExecutionProvenance,
) -> None:
    registry = _InitiallySuccessfulRegistry()

    record = await AutoGenRuntimeAdapter(
        registry, provenance=execution_provenance
    ).execute(_scenario("clean-01"), _config())

    assert record.status is ExecutionStatus.SUCCEEDED
    assert record.final_message == "Environment reported successful completion."
    assert registry.environment is not None
    assert registry.environment.decide_calls == 0


@pytest.mark.asyncio
async def test_autogen_initial_incompatibility_uses_environment_typed_failure(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await AutoGenRuntimeAdapter(
        _InitiallyIncompatibleRegistry(), provenance=execution_provenance
    ).execute(_scenario("clean-01"), _config())

    assert record.status is ExecutionStatus.INCOMPATIBLE
    assert record.failure is not None
    assert record.failure.category is RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY
    assert record.failure.code == "environment_incompatible"


@pytest.mark.asyncio
async def test_autogen_untyped_exception_maps_without_leaking_message(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await AutoGenRuntimeAdapter(
        _ExplodingRegistry(), provenance=execution_provenance
    ).execute(_scenario("clean-01"), _config())

    assert record.status is ExecutionStatus.FAILED
    assert record.failure is not None
    assert record.failure.category is RuntimeFailureCategory.FRAMEWORK_EXECUTION
    assert record.failure.code == "framework_exception"
    assert "unsanitized" not in record.model_dump_json()


@pytest.mark.asyncio
async def test_autogen_untyped_exception_mapping_matches_langgraph(
    execution_provenance: ExecutionProvenance,
) -> None:
    scenario = _scenario("clean-01")
    config = _config()

    autogen_record = await AutoGenRuntimeAdapter(
        _ExplodingRegistry(), provenance=execution_provenance
    ).execute(scenario, config)
    langgraph_record = await LangGraphRuntimeAdapter(
        _ExplodingRegistry(), provenance=execution_provenance
    ).execute(scenario, config)

    assert autogen_record.status is langgraph_record.status
    assert autogen_record.failure is not None
    assert langgraph_record.failure is not None
    assert autogen_record.failure.category is langgraph_record.failure.category
    assert autogen_record.failure.code == langgraph_record.failure.code
    assert autogen_record.failure.retryable is langgraph_record.failure.retryable


@pytest.mark.asyncio
async def test_autogen_terminal_transition_after_decision_skips_tool_agent(
    execution_provenance: ExecutionProvenance,
) -> None:
    registry = _TerminalAfterDecisionRegistry()

    record = await AutoGenRuntimeAdapter(
        registry, provenance=execution_provenance
    ).execute(_scenario("clean-01"), _config())

    assert record.status is ExecutionStatus.FAILED
    assert record.failure is not None
    assert record.failure.code == "terminal_failure"
    assert registry.environment is not None
    assert registry.environment.execute_calls == 0


@pytest.mark.asyncio
async def test_autogen_terminal_success_at_the_limit_precedes_budget_failure(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await AutoGenRuntimeAdapter(
        _SuccessfulToolAtLimitRegistry(), provenance=execution_provenance
    ).execute(
        _scenario("clean-01"),
        _config(max_steps=1, max_tool_calls=1),
    )

    assert record.status is ExecutionStatus.SUCCEEDED
    assert record.steps == 1
    assert record.tool_calls == 1


class _CountingRegistry:
    def __init__(self, factory: Callable[[LabScenario], LabEnvironment]) -> None:
        self.factory = factory
        self.environments: list[LabEnvironment] = []

    def build(self, scenario: LabScenario) -> LabEnvironment:
        environment = self.factory(scenario)
        self.environments.append(environment)
        return environment


@pytest.mark.asyncio
async def test_autogen_each_execution_builds_fresh_state_and_isolated_traces(
    execution_provenance: ExecutionProvenance,
) -> None:
    registry = _CountingRegistry(SupportLabEnvironmentRegistry().build)
    adapter = AutoGenRuntimeAdapter(registry, provenance=execution_provenance)
    scenario = _scenario("clean-01")

    first, second = await asyncio.gather(
        adapter.execute(scenario, _config()),
        adapter.execute(scenario, _config(repetition=2)),
    )

    assert first.status is ExecutionStatus.SUCCEEDED
    assert second.status is ExecutionStatus.SUCCEEDED
    assert len(registry.environments) == 2
    assert registry.environments[0] is not registry.environments[1]
    assert first.trace.trace_id != second.trace.trace_id
    for record in (first, second):
        root = record.trace.spans[0]
        assert root.parent_span_id is None
        assert all(span.parent_span_id == root.span_id for span in record.trace.spans[1:])


def _agent_session() -> tuple[AutoGenLabSession, InMemorySpanExporter]:
    tracer, exporter = build_run_tracer("spanvouch.tests.autogen")
    session = AutoGenLabSession(
        environment=SupportLabEnvironmentRegistry().build(_scenario("clean-01")),
        run_config=_config(),
        tracer=tracer,
    )
    return session, exporter


@pytest.mark.asyncio
async def test_autogen_tool_agent_rejects_final_action() -> None:
    session, exporter = _agent_session()
    agent = EnvironmentToolAgent(session)
    message = TextMessage(
        content=canonical_json(AgentAction(kind="final", final_message="done")),
        source="spanvouch_lab_agent",
    )

    with pytest.raises(RuntimeError, match="final action"):
        await agent.on_messages([message], CancellationToken())

    exporter.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "messages, expected",
    [
        ([], "requires a TextMessage"),
        ([TextMessage(content="not-json", source="agent")], "invalid action"),
    ],
)
async def test_autogen_tool_agent_rejects_malformed_actions(
    messages: list[TextMessage],
    expected: str,
) -> None:
    session, exporter = _agent_session()
    agent = EnvironmentToolAgent(session)

    with pytest.raises(RuntimeError, match=expected):
        await agent.on_messages(messages, CancellationToken())

    exporter.clear()


@pytest.mark.asyncio
async def test_autogen_action_agent_honors_pre_cancelled_token() -> None:
    session, exporter = _agent_session()
    agent = EnvironmentActionAgent(session)
    token = CancellationToken()
    token.cancel()

    with pytest.raises(asyncio.CancelledError):
        await agent.on_messages([], token)

    exporter.clear()


@pytest.mark.asyncio
async def test_autogen_action_agent_rejects_terminal_session() -> None:
    session, exporter = _agent_session()
    session.state = session.state.with_final("already complete")
    agent = EnvironmentActionAgent(session)

    with pytest.raises(RuntimeError, match="scheduled after terminal"):
        await agent.on_messages([], CancellationToken())

    exporter.clear()


@pytest.mark.asyncio
async def test_autogen_cancelling_before_team_creation_still_marks_token() -> None:
    token = CancellationToken()

    await autogen_runtime._cancel_team_run(None, token)

    assert token.is_cancelled()


def test_autogen_invalid_agent_message_does_not_terminate_active_session() -> None:
    session, exporter = _agent_session()

    terminated = autogen_runtime._should_terminate(
        session,
        [TextMessage(content="not-json", source="spanvouch_lab_agent")],
    )

    assert terminated is False
    exporter.clear()


def test_autogen_non_terminal_team_result_maps_to_typed_failure() -> None:
    session, exporter = _agent_session()

    autogen_result = autogen_runtime._result_from_state(
        session.environment,
        RuntimeState.initial(),
        session.run_config,
    )
    langgraph_result = langgraph_runtime._result_from_state(
        session.environment,
        RuntimeState.initial(),
        session.run_config,
    )

    assert autogen_result.status is langgraph_result.status is ExecutionStatus.FAILED
    assert autogen_result.failure is not None
    assert langgraph_result.failure is not None
    assert autogen_result.failure.code == langgraph_result.failure.code
    assert autogen_result.failure.code == "non_terminal_result"
    exporter.clear()


def test_autogen_nested_trace_attribute_is_serialized() -> None:
    assert autogen_runtime._trace_attribute(["nested"]) == "['nested']"


@pytest.mark.parametrize("terminal_kind", ["failure", "final"])
def test_autogen_infrastructure_failure_replaces_prior_terminal_state(
    terminal_kind: str,
) -> None:
    original_failure = RuntimeFailure.from_message(
        category=RuntimeFailureCategory.FRAMEWORK_EXECUTION,
        code="original",
        retryable=False,
        sanitized_message="original",
    )
    infrastructure_failure = RuntimeFailure.from_message(
        category=RuntimeFailureCategory.INFRASTRUCTURE,
        code="cancelled",
        retryable=True,
        sanitized_message="cancelled",
    )
    state = RuntimeState.initial()
    state = (
        state.with_failure(original_failure)
        if terminal_kind == "failure"
        else state.with_final("done")
    )

    replaced = autogen_runtime._replace_terminal_with_failure(
        state,
        infrastructure_failure,
    )

    assert replaced.failure == infrastructure_failure
    assert replaced.final_message is None
