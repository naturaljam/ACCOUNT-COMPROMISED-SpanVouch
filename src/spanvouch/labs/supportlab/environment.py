from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import cast

from pydantic import JsonValue

from spanvouch.contracts.sanitization import sanitize_diagnostic_value
from spanvouch.labs.runtime import (
    AgentAction,
    ExecutionStatus,
    LabScenario,
    RuntimeFailure,
    RuntimeFailureCategory,
    RuntimeState,
    ToolObservation,
)
from spanvouch.labs.supportlab.decision import (
    DecisionContext,
    DecisionKind,
    DecisionModel,
    ScriptedDecisionModel,
)
from spanvouch.labs.supportlab.policy import Approval
from spanvouch.labs.supportlab.repository import build_seed_repository
from spanvouch.labs.supportlab.runtime import build_support_lab_scenarios
from spanvouch.labs.supportlab.tools import RefundRejected, SupportTools

_MAX_STEPS = 8


class FrameworkIncompatibilityError(RuntimeError):
    def __init__(self, failure: RuntimeFailure) -> None:
        super().__init__(failure.code)
        self.failure = failure


@dataclass(frozen=True)
class _DecisionFault:
    wrong_tool: bool
    invalid_amount: bool
    skip_policy: bool
    ignore_tool_error: bool
    poisoned_context: bool
    bypass_approval: bool
    repeat_lookup: bool
    false_success: bool


@dataclass(frozen=True)
class _DecisionScenario:
    scenario_id: str
    customer_id: str
    order_id: str
    fault: _DecisionFault

    @classmethod
    def from_lab(cls, scenario: LabScenario) -> _DecisionScenario:
        return cls(
            scenario_id=scenario.scenario_id,
            customer_id=_string_parameter(scenario, "customer_id"),
            order_id=_string_parameter(scenario, "order_id"),
            fault=_DecisionFault(
                wrong_tool=_bool_injection(scenario, "wrong_tool"),
                invalid_amount=_bool_injection(scenario, "invalid_amount"),
                skip_policy=_bool_injection(scenario, "skip_policy"),
                ignore_tool_error=_bool_injection(scenario, "ignore_tool_error"),
                poisoned_context=_bool_injection(scenario, "poisoned_context"),
                bypass_approval=_bool_injection(scenario, "bypass_approval"),
                repeat_lookup=_bool_injection(scenario, "repeat_lookup"),
                false_success=_bool_injection(scenario, "false_success"),
            ),
        )


class SupportLabEnvironment:
    def __init__(
        self,
        *,
        scenario: LabScenario,
        tools: SupportTools,
        decision_model: DecisionModel | None = None,
        max_steps: int = _MAX_STEPS,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.scenario = scenario
        self._tools = tools
        self._decision_model = decision_model or ScriptedDecisionModel(
            _DecisionScenario.from_lab(scenario)
        )
        self._max_steps = max_steps

    async def decide(self, state: RuntimeState) -> AgentAction:
        observations = tuple(_observation_text(item) for item in state.observations)
        decision = await self._decision_model.next_decision(
            DecisionContext(step=state.step, observations=observations)
        )
        if decision.kind is DecisionKind.FINAL:
            if decision.message is None:
                raise ValueError("final decision requires a message")
            return AgentAction(kind="final", final_message=decision.message)
        if decision.tool_name is None:
            raise ValueError("tool decision requires a tool name")
        return AgentAction(
            kind="tool",
            tool_name=decision.tool_name,
            arguments=cast(dict[str, JsonValue], decision.arguments),
        )

    async def execute(self, action: AgentAction) -> ToolObservation:
        if action.kind != "tool" or action.tool_name is None:
            raise ValueError("SupportLab can execute only tool actions")
        arguments = action.arguments
        tool_name = action.tool_name
        observation_tool_name = _sanitize_text(tool_name)
        try:
            result = await self._dispatch(tool_name, arguments)
        except (InvalidOperation, KeyError, RefundRejected, ValueError) as error:
            error_type = type(error).__name__
            error_message = _sanitize_text(str(error))
            ignored = arguments.get("ignore_error") == "true"
            return ToolObservation(
                tool_name=observation_tool_name,
                error={"type": error_type, "message": error_message},
                status="error",
                retryable=ignored,
            )
        return ToolObservation(
            tool_name=observation_tool_name,
            result=_sanitize_text(str(result)),
            status="ok",
            retryable=False,
        )

    def terminal_status(self, state: RuntimeState) -> ExecutionStatus | None:
        if state.final_message is not None:
            return ExecutionStatus.SUCCEEDED
        if state.failure is not None:
            if (
                state.failure.category
                is RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY
            ):
                return ExecutionStatus.INCOMPATIBLE
            return ExecutionStatus.FAILED
        if state.observations and (
            state.observations[-1].status == "error"
            and not state.observations[-1].retryable
        ):
            return ExecutionStatus.FAILED
        if state.step >= self._max_steps:
            return ExecutionStatus.STEP_LIMIT
        return None

    async def _dispatch(
        self, tool_name: str, arguments: dict[str, JsonValue]
    ) -> object:
        if tool_name == "get_customer":
            return await self._tools.get_customer(_string_argument(arguments, "customer_id"))
        if tool_name == "get_order":
            return await self._tools.get_order(_string_argument(arguments, "order_id"))
        if tool_name == "get_refund_policy":
            return await self._tools.get_refund_policy(
                _string_argument(arguments, "order_id")
            )
        if tool_name == "calculate_refund":
            return await self._tools.calculate_refund(
                _string_argument(arguments, "order_id"),
                _item_skus(arguments),
            )
        if tool_name == "submit_refund":
            approval_value = _string_argument(arguments, "approval")
            approval = (
                None
                if approval_value == "none"
                else Approval(approved_by=approval_value)
            )
            return await self._tools.submit_refund(
                customer_id=_string_argument(arguments, "customer_id"),
                order_id=_string_argument(arguments, "order_id"),
                amount=Decimal(_string_argument(arguments, "amount")),
                item_skus=_item_skus(arguments),
                reason=_string_argument(arguments, "reason"),
                idempotency_key=_string_argument(arguments, "idempotency_key"),
                approval=approval,
            )
        raise KeyError(f"unknown tool: {tool_name}")


class SupportLabEnvironmentRegistry:
    def __init__(self, *, max_steps: int = _MAX_STEPS) -> None:
        self._max_steps = max_steps
        self._scenario_ids = frozenset(
            item.scenario_id for item in build_support_lab_scenarios()
        )

    def build(self, scenario: LabScenario) -> SupportLabEnvironment:
        if scenario.domain != "supportlab":
            raise _incompatibility("unsupported_domain", scenario.domain)
        if scenario.scenario_id not in self._scenario_ids:
            raise _incompatibility("unsupported_scenario", scenario.scenario_id)
        return SupportLabEnvironment(
            scenario=scenario,
            tools=SupportTools(build_seed_repository()),
            max_steps=self._max_steps,
        )


def _incompatibility(code: str, value: str) -> FrameworkIncompatibilityError:
    return FrameworkIncompatibilityError(
        RuntimeFailure.from_message(
            category=RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY,
            code=code,
            retryable=False,
            sanitized_message=_sanitize_text(value),
        )
    )


def _string_parameter(scenario: LabScenario, name: str) -> str:
    value = scenario.parameters.get(name)
    if not isinstance(value, str):
        raise ValueError(f"SupportLab parameter {name} must be a string")
    return value


def _bool_injection(scenario: LabScenario, name: str) -> bool:
    value = scenario.injection.get(name, False)
    if not isinstance(value, bool):
        raise ValueError(f"SupportLab injection {name} must be a boolean")
    return value


def _string_argument(arguments: dict[str, JsonValue], name: str) -> str:
    value = arguments[name]
    if not isinstance(value, str):
        raise ValueError(f"SupportLab argument {name} must be a string")
    return value


def _item_skus(arguments: dict[str, JsonValue]) -> tuple[str, ...]:
    return tuple(_string_argument(arguments, "item_skus").split(","))


def _sanitize_text(value: str) -> str:
    return cast(str, sanitize_diagnostic_value(value))


def _observation_text(observation: ToolObservation) -> str:
    if observation.status == "ok":
        return cast(str, observation.result)
    error = cast(dict[str, JsonValue], observation.error)
    return f"ERROR:{error['type']}:{error['message']}"
