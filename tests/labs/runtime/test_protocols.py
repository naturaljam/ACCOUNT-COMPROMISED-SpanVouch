from __future__ import annotations

from typing import get_type_hints

from spanvouch.labs.runtime.models import (
    AgentAction,
    ExecutionRecord,
    ExecutionStatus,
    FrameworkId,
    LabScenario,
    RuntimeConfig,
    RuntimeState,
    ToolObservation,
)
from spanvouch.labs.runtime.protocols import (
    AgentRuntimeAdapter,
    LabEnvironment,
    LabEnvironmentRegistry,
)


def test_runtime_adapter_has_the_frozen_port() -> None:
    hints = get_type_hints(AgentRuntimeAdapter.execute)
    assert hints["scenario"] is LabScenario
    assert hints["run_config"] is RuntimeConfig
    assert hints["return"] == ExecutionRecord


def test_lab_environment_has_the_frozen_port() -> None:
    decide = get_type_hints(LabEnvironment.decide)
    execute = get_type_hints(LabEnvironment.execute)
    terminal_status = get_type_hints(LabEnvironment.terminal_status)

    assert get_type_hints(LabEnvironment)["scenario"] is LabScenario
    assert decide == {"state": RuntimeState, "return": AgentAction}
    assert execute == {"action": AgentAction, "return": ToolObservation}
    assert terminal_status == {
        "state": RuntimeState,
        "return": ExecutionStatus | None,
    }


def test_lab_environment_registry_has_the_frozen_port() -> None:
    hints = get_type_hints(LabEnvironmentRegistry.build)
    assert hints == {"scenario": LabScenario, "return": LabEnvironment}


def test_runtime_adapter_framework_metadata_is_typed() -> None:
    hints = get_type_hints(AgentRuntimeAdapter)
    assert hints == {
        "framework_id": FrameworkId,
        "framework_version": str,
    }
