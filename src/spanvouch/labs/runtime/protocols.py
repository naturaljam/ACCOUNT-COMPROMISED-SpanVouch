from typing import Protocol

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


class LabEnvironment(Protocol):
    scenario: LabScenario

    async def decide(self, state: RuntimeState) -> AgentAction: ...

    async def execute(self, action: AgentAction) -> ToolObservation: ...

    def terminal_status(self, state: RuntimeState) -> ExecutionStatus | None: ...


class LabEnvironmentRegistry(Protocol):
    def build(self, scenario: LabScenario) -> LabEnvironment: ...


class AgentRuntimeAdapter(Protocol):
    framework_id: FrameworkId
    framework_version: str

    async def execute(
        self,
        scenario: LabScenario,
        run_config: RuntimeConfig,
    ) -> ExecutionRecord: ...

