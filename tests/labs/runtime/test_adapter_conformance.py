from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.labs.frameworks.autogen import AutoGenRuntimeAdapter
from spanvouch.labs.frameworks.langgraph import LangGraphRuntimeAdapter
from spanvouch.labs.runtime import (
    AgentAction,
    AgentRuntimeAdapter,
    ExecutionProvenance,
    ExecutionRecord,
    ExecutionStatus,
    FrameworkId,
    LabEnvironment,
    LabEnvironmentRegistry,
    LabScenario,
    RuntimeConfig,
    RuntimeFailureCategory,
    RuntimeState,
    ToolObservation,
)
from spanvouch.labs.supportlab.environment import SupportLabEnvironmentRegistry
from spanvouch.labs.supportlab.runtime import build_support_lab_scenarios

type AdapterFactory = Callable[
    [LabEnvironmentRegistry, ExecutionProvenance], AgentRuntimeAdapter
]


def _langgraph_factory(
    registry: LabEnvironmentRegistry, provenance: ExecutionProvenance
) -> AgentRuntimeAdapter:
    return LangGraphRuntimeAdapter(registry, provenance=provenance)


def _autogen_factory(
    registry: LabEnvironmentRegistry, provenance: ExecutionProvenance
) -> AgentRuntimeAdapter:
    return AutoGenRuntimeAdapter(registry, provenance=provenance)


ADAPTER_FACTORIES: tuple[tuple[str, AdapterFactory, FrameworkId], ...] = (
    ("langgraph", _langgraph_factory, FrameworkId.LANGGRAPH),
    ("autogen", _autogen_factory, FrameworkId.AUTOGEN),
)
_TRIGGER_ID_BY_FAMILY = {
    "clean": "none",
    "wrong_tool": "decision.0",
    "invalid_argument": "decision.4",
    "missing_precondition": "decision.2",
    "ignored_tool_error": "decision.4",
    "context_corruption": "decision.4",
    "policy_violation": "decision.4",
    "loop_or_budget_exhaustion": "decision.0",
    "invalid_final_state": "decision.5",
}


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


def _config(**overrides: int | float) -> RuntimeConfig:
    values: dict[str, int | float] = {
        "seed": 20260719,
        "repetition": 1,
        "max_steps": 10,
        "timeout_seconds": 5.0,
        "max_retries": 0,
        "max_tool_calls": 10,
    }
    values.update(overrides)
    return RuntimeConfig.model_validate(values)


def _scenario(scenario_id: str) -> LabScenario:
    return next(
        scenario
        for scenario in build_support_lab_scenarios()
        if scenario.scenario_id == scenario_id
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_name", "factory", "framework_id"),
    ADAPTER_FACTORIES,
    ids=[item[0] for item in ADAPTER_FACTORIES],
)
async def test_complete_supportlab_executes_as_hashed_typed_records(
    adapter_name: str,
    factory: AdapterFactory,
    framework_id: FrameworkId,
    provenance: ExecutionProvenance,
) -> None:
    del adapter_name
    config = _config()
    adapter = factory(SupportLabEnvironmentRegistry(), provenance)

    records = [
        await adapter.execute(scenario, config)
        for scenario in build_support_lab_scenarios()
    ]

    assert len(records) == 20
    assert {record.failure_family for record in records} == {
        "clean",
        "wrong_tool",
        "invalid_argument",
        "missing_precondition",
        "ignored_tool_error",
        "context_corruption",
        "policy_violation",
        "loop_or_budget_exhaustion",
        "invalid_final_state",
    }
    for scenario, record in zip(build_support_lab_scenarios(), records, strict=True):
        assert isinstance(record, ExecutionRecord)
        assert record.framework_id is framework_id
        assert record.scenario_id == scenario.scenario_id
        assert record.template_id == scenario.template_id
        assert record.seed == config.seed
        assert record.runtime_config == config
        assert record.runtime_config_sha256 == canonical_sha256(config)
        assert record.scenario_input_sha256 == canonical_sha256(
            {
                "user_request": scenario.user_request,
                "parameters": scenario.parameters,
                "tool_contract_sha256": scenario.tool_contract_sha256,
            }
        )
        expected_trigger_id = _TRIGGER_ID_BY_FAMILY[scenario.failure_family]
        assert record.injection_trigger_id == expected_trigger_id
        assert record.injection_trigger_sha256 == scenario.injection_trigger_digest(
            expected_trigger_id
        )
        assert record.terminal_predicate_sha256 == canonical_sha256(
            scenario.terminal_predicate_id
        )
        assert record.evidence_selector_sha256 == canonical_sha256(
            list(scenario.allowed_evidence_selectors)
        )
        assert record.trace.run_id == scenario.scenario_id
        assert record.trace_sha256 == canonical_sha256(record.trace)
        injection_markers = tuple(
            span.attributes
            for span in record.trace.spans
            if "injection.trigger.id" in span.attributes
        )
        if scenario.failure_family == "clean":
            assert injection_markers == ()
        else:
            assert len(injection_markers) == 1
            assert injection_markers[0] == {
                "injection.trigger.id": expected_trigger_id,
                "injection.trigger.sha256": record.injection_trigger_sha256,
            }
    clean_records = tuple(record for record in records if record.failure_family == "clean")
    assert all(record.status is ExecutionStatus.SUCCEEDED for record in clean_records)


class _BlockingEnvironment:
    def __init__(
        self,
        scenario: LabScenario,
        started: asyncio.Event,
        cleaned: asyncio.Event,
    ) -> None:
        self.scenario = scenario
        self._started = started
        self._cleaned = cleaned

    async def decide(self, state: RuntimeState) -> AgentAction:
        del state
        self._started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self._cleaned.set()
        raise AssertionError("unreachable")

    async def execute(self, action: AgentAction) -> ToolObservation:
        del action
        raise AssertionError("unreachable")

    def terminal_status(self, state: RuntimeState) -> ExecutionStatus | None:
        del state
        return None


class _BlockingRegistry:
    def __init__(self, started: asyncio.Event, cleaned: asyncio.Event) -> None:
        self._started = started
        self._cleaned = cleaned

    def build(self, scenario: LabScenario) -> LabEnvironment:
        return _BlockingEnvironment(scenario, self._started, self._cleaned)


class _ExplodingRegistry:
    def build(self, scenario: LabScenario) -> LabEnvironment:
        del scenario
        raise RuntimeError("provider-secret-should-never-escape")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_name", "factory", "framework_id"),
    ADAPTER_FACTORIES,
    ids=[item[0] for item in ADAPTER_FACTORIES],
)
async def test_limits_exceptions_and_timeout_are_typed(
    adapter_name: str,
    factory: AdapterFactory,
    framework_id: FrameworkId,
    provenance: ExecutionProvenance,
) -> None:
    del adapter_name, framework_id
    scenario = _scenario("loop_or_budget_exhaustion-01")
    step_limited = await factory(SupportLabEnvironmentRegistry(), provenance).execute(
        scenario, _config(max_steps=2)
    )
    tool_limited = await factory(SupportLabEnvironmentRegistry(), provenance).execute(
        _scenario("clean-01"), _config(max_tool_calls=2)
    )
    exploded = await factory(_ExplodingRegistry(), provenance).execute(
        _scenario("clean-01"), _config()
    )
    started = asyncio.Event()
    cleaned = asyncio.Event()
    timed_out = await factory(
        _BlockingRegistry(started, cleaned), provenance
    ).execute(_scenario("clean-01"), _config(timeout_seconds=0.01))

    assert step_limited.status is ExecutionStatus.STEP_LIMIT
    assert step_limited.steps == 2
    assert step_limited.failure is not None
    assert step_limited.failure.code == "step_limit"
    assert tool_limited.status is ExecutionStatus.STEP_LIMIT
    assert tool_limited.tool_calls == 2
    assert tool_limited.failure is not None
    assert tool_limited.failure.code == "tool_call_limit"
    assert exploded.status is ExecutionStatus.FAILED
    assert exploded.failure is not None
    assert exploded.failure.category is RuntimeFailureCategory.FRAMEWORK_EXECUTION
    assert exploded.failure.code == "framework_exception"
    assert "provider-secret" not in exploded.model_dump_json()
    assert timed_out.status is ExecutionStatus.FAILED
    assert timed_out.failure is not None
    assert timed_out.failure.category is RuntimeFailureCategory.INFRASTRUCTURE
    assert timed_out.failure.code == "timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_name", "factory", "framework_id"),
    ADAPTER_FACTORIES,
    ids=[item[0] for item in ADAPTER_FACTORIES],
)
async def test_external_cancellation_propagates_after_cleanup(
    adapter_name: str,
    factory: AdapterFactory,
    framework_id: FrameworkId,
    provenance: ExecutionProvenance,
) -> None:
    del adapter_name, framework_id
    started = asyncio.Event()
    cleaned = asyncio.Event()
    adapter = factory(_BlockingRegistry(started, cleaned), provenance)
    task = asyncio.create_task(adapter.execute(_scenario("clean-01"), _config()))
    await started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert cleaned.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_name", "factory", "framework_id"),
    ADAPTER_FACTORIES,
    ids=[item[0] for item in ADAPTER_FACTORIES],
)
async def test_second_run_is_fresh_and_serialization_is_stage_a_safe(
    adapter_name: str,
    factory: AdapterFactory,
    framework_id: FrameworkId,
    provenance: ExecutionProvenance,
) -> None:
    del adapter_name, framework_id
    adapter = factory(SupportLabEnvironmentRegistry(), provenance)
    first = await adapter.execute(_scenario("wrong_tool-01"), _config())
    second = await adapter.execute(_scenario("clean-01"), _config(repetition=2))

    assert first.status is ExecutionStatus.FAILED
    assert second.status is ExecutionStatus.SUCCEEDED
    assert second.failure is None
    assert second.steps == second.tool_calls == 5
    assert first.trace.trace_id != second.trace.trace_id
    serialized = second.model_dump_json().lower()
    for forbidden in (
        "gold_label",
        "expected_failure",
        "expected_finding",
        "mutation_metadata",
        "split_identity",
        "api_key",
        "authorization",
        "raw_response",
        "prompt_text",
        "hidden_reasoning",
    ):
        assert forbidden not in serialized
