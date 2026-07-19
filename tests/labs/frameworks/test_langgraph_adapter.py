import asyncio
from collections.abc import Callable
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer

from spanvouch.contracts.trace import SpanKind
from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.labs.frameworks.langgraph import LangGraphRuntimeAdapter
from spanvouch.labs.runtime import (
    AgentAction,
    ExecutionProvenance,
    ExecutionStatus,
    FrameworkId,
    LabEnvironment,
    LabScenario,
    RuntimeConfig,
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
async def test_langgraph_adapter_returns_a_hashed_contract_valid_record(
    execution_provenance: ExecutionProvenance,
) -> None:
    scenario = _scenario("clean-01")
    adapter = LangGraphRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    )

    record = await adapter.execute(scenario, _config())

    assert record.framework_id is FrameworkId.LANGGRAPH
    assert record.status is ExecutionStatus.SUCCEEDED
    assert record.tool_calls == 5
    assert record.trace_sha256 == canonical_sha256(record.trace)
    assert record.trace.spans[0].name == "supportlab.run"
    assert record.framework_version
    workflow_spans = tuple(
        span for span in record.trace.spans if span.kind is SpanKind.WORKFLOW
    )
    assert len(workflow_spans) == 6


@pytest.mark.asyncio
async def test_step_limit_maps_to_one_framework_execution_failure(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await LangGraphRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    ).execute(_scenario("loop_or_budget_exhaustion-01"), _config(max_steps=2))

    assert record.status is ExecutionStatus.STEP_LIMIT
    assert record.steps == 2
    assert record.failure is not None
    assert record.failure.category is RuntimeFailureCategory.FRAMEWORK_EXECUTION
    assert record.failure.code == "step_limit"


@pytest.mark.asyncio
async def test_run_config_step_limit_overrides_the_environment_default(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await LangGraphRuntimeAdapter(
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
async def test_tool_call_limit_maps_to_one_framework_execution_failure(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await LangGraphRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    ).execute(_scenario("clean-01"), _config(max_tool_calls=2))

    assert record.status is ExecutionStatus.STEP_LIMIT
    assert record.tool_calls == 2
    assert record.failure is not None
    assert record.failure.category is RuntimeFailureCategory.FRAMEWORK_EXECUTION
    assert record.failure.code == "tool_call_limit"


@pytest.mark.asyncio
async def test_unknown_tool_maps_to_one_framework_execution_failure(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await LangGraphRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    ).execute(_scenario("wrong_tool-01"), _config())

    assert record.status is ExecutionStatus.FAILED
    assert record.failure is not None
    assert record.failure.category is RuntimeFailureCategory.FRAMEWORK_EXECUTION
    assert record.failure.code == "tool_error"


@pytest.mark.asyncio
async def test_tool_failure_at_the_configured_limit_remains_a_tool_failure(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await LangGraphRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    ).execute(
        _scenario("wrong_tool-01"),
        _config(max_steps=1, max_tool_calls=1),
    )

    assert record.status is ExecutionStatus.FAILED
    assert record.failure is not None
    assert record.failure.code == "tool_error"


@pytest.mark.asyncio
async def test_ignored_tool_error_remains_a_success_without_a_failure(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await LangGraphRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    ).execute(_scenario("ignored_tool_error-01"), _config())

    assert record.status is ExecutionStatus.SUCCEEDED
    assert record.failure is None
    assert record.tool_calls == 5


@pytest.mark.asyncio
async def test_environment_incompatibility_maps_to_one_typed_failure(
    execution_provenance: ExecutionProvenance,
) -> None:
    scenario = _scenario("clean-01").model_copy(update={"domain": "opslab"})

    record = await LangGraphRuntimeAdapter(
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
        raise AssertionError("successful terminal state must not schedule execute")

    def terminal_status(self, state: RuntimeState) -> ExecutionStatus | None:
        return ExecutionStatus.SUCCEEDED


class _InitiallySuccessfulRegistry:
    def __init__(self) -> None:
        self.environment: _InitiallySuccessfulEnvironment | None = None

    def build(self, scenario: LabScenario) -> _InitiallySuccessfulEnvironment:
        self.environment = _InitiallySuccessfulEnvironment(scenario)
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
async def test_timeout_maps_to_one_infrastructure_failure(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await LangGraphRuntimeAdapter(
        _BlockingRegistry(asyncio.Event()), provenance=execution_provenance
    ).execute(_scenario("clean-01"), _config(timeout_seconds=0.01))

    assert record.status is ExecutionStatus.FAILED
    assert record.failure is not None
    assert record.failure.category is RuntimeFailureCategory.INFRASTRUCTURE
    assert record.failure.code == "timeout"
    assert record.trace.spans[0].name == "supportlab.run"


@pytest.mark.asyncio
async def test_initial_terminal_state_reaches_end_without_scheduling_decide(
    execution_provenance: ExecutionProvenance,
) -> None:
    registry = _InitiallyTerminalRegistry()

    record = await LangGraphRuntimeAdapter(
        registry, provenance=execution_provenance
    ).execute(_scenario("clean-01"), _config())

    assert record.status is ExecutionStatus.FAILED
    assert record.failure is not None
    assert record.failure.code == "terminal_failure"
    assert registry.environment is not None
    assert registry.environment.decide_calls == 0


@pytest.mark.asyncio
async def test_initial_success_state_reaches_end_without_scheduling_decide(
    execution_provenance: ExecutionProvenance,
) -> None:
    registry = _InitiallySuccessfulRegistry()

    record = await LangGraphRuntimeAdapter(
        registry, provenance=execution_provenance
    ).execute(_scenario("clean-01"), _config())

    assert record.status is ExecutionStatus.SUCCEEDED
    assert record.final_message == "Environment reported successful completion."
    assert registry.environment is not None
    assert registry.environment.decide_calls == 0


@pytest.mark.asyncio
async def test_successful_tool_at_the_configured_limit_remains_successful(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await LangGraphRuntimeAdapter(
        _SuccessfulToolAtLimitRegistry(), provenance=execution_provenance
    ).execute(
        _scenario("clean-01"),
        _config(max_steps=1, max_tool_calls=1),
    )

    assert record.status is ExecutionStatus.SUCCEEDED
    assert record.steps == 1
    assert record.tool_calls == 1


@pytest.mark.asyncio
async def test_terminal_transition_after_decision_does_not_schedule_execute(
    execution_provenance: ExecutionProvenance,
) -> None:
    registry = _TerminalAfterDecisionRegistry()

    record = await LangGraphRuntimeAdapter(
        registry, provenance=execution_provenance
    ).execute(_scenario("clean-01"), _config())

    assert record.status is ExecutionStatus.FAILED
    assert record.failure is not None
    assert record.failure.code == "terminal_failure"
    assert registry.environment is not None
    assert registry.environment.execute_calls == 0


@pytest.mark.asyncio
async def test_timeout_preserves_completed_runtime_progress(
    execution_provenance: ExecutionProvenance,
) -> None:
    record = await LangGraphRuntimeAdapter(
        _PartiallyBlockingRegistry(), provenance=execution_provenance
    ).execute(_scenario("clean-01"), _config(timeout_seconds=0.1))

    assert record.status is ExecutionStatus.FAILED
    assert record.steps == 1
    assert record.tool_calls == 1


@pytest.mark.asyncio
async def test_cancellation_maps_to_one_infrastructure_failure(
    execution_provenance: ExecutionProvenance,
) -> None:
    started = asyncio.Event()
    adapter = LangGraphRuntimeAdapter(
        _BlockingRegistry(started), provenance=execution_provenance
    )
    task = asyncio.create_task(adapter.execute(_scenario("clean-01"), _config()))
    await started.wait()

    task.cancel()
    record = await task

    assert record.status is ExecutionStatus.FAILED
    assert record.failure is not None
    assert record.failure.category is RuntimeFailureCategory.INFRASTRUCTURE
    assert record.failure.code == "cancelled"


class _CountingRegistry:
    def __init__(self, factory: Callable[[LabScenario], LabEnvironment]) -> None:
        self.factory = factory
        self.environments: list[LabEnvironment] = []

    def build(self, scenario: LabScenario) -> LabEnvironment:
        environment = self.factory(scenario)
        self.environments.append(environment)
        return environment


@pytest.mark.asyncio
async def test_each_execution_builds_a_fresh_environment(
    execution_provenance: ExecutionProvenance,
) -> None:
    registry = _CountingRegistry(SupportLabEnvironmentRegistry().build)
    adapter = LangGraphRuntimeAdapter(registry, provenance=execution_provenance)
    scenario = _scenario("clean-01")

    first = await adapter.execute(scenario, _config())
    second = await adapter.execute(scenario, _config(repetition=2))

    assert first.status is ExecutionStatus.SUCCEEDED
    assert second.status is ExecutionStatus.SUCCEEDED
    assert len(registry.environments) == 2
    assert registry.environments[0] is not registry.environments[1]


@pytest.mark.asyncio
async def test_concurrent_executions_have_isolated_parented_traces(
    execution_provenance: ExecutionProvenance,
) -> None:
    adapter = LangGraphRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    )
    scenario = _scenario("clean-01")

    first, second = await asyncio.gather(
        adapter.execute(scenario, _config()),
        adapter.execute(scenario, _config(repetition=2)),
    )

    assert first.trace.trace_id != second.trace.trace_id
    for record in (first, second):
        root = record.trace.spans[0]
        assert root.parent_span_id is None
        assert all(span.parent_span_id == root.span_id for span in record.trace.spans[1:])


@pytest.mark.asyncio
async def test_adapter_consumes_the_run_exporter_after_trace_mapping(
    execution_provenance: ExecutionProvenance,
) -> None:
    exporters: list[InMemorySpanExporter] = []

    def capture_exporter(
        service_name: str,
    ) -> tuple[Tracer, InMemorySpanExporter]:
        tracer, exporter = build_run_tracer(service_name)
        exporters.append(exporter)
        return tracer, exporter

    with patch(
        "spanvouch.labs.frameworks.langgraph.build_run_tracer",
        side_effect=capture_exporter,
    ):
        record = await LangGraphRuntimeAdapter(
            SupportLabEnvironmentRegistry(), provenance=execution_provenance
        ).execute(_scenario("clean-01"), _config())

    assert record.status is ExecutionStatus.SUCCEEDED
    assert len(exporters) == 1
    assert exporters[0].get_finished_spans() == ()
