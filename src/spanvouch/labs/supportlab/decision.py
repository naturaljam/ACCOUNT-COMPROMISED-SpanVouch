from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from spanvouch.labs.supportlab.scenarios import Scenario


class DecisionKind(StrEnum):
    TOOL = "tool"
    FINAL = "final"


class AgentDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: DecisionKind
    tool_name: str | None = None
    arguments: dict[str, str] = Field(default_factory=dict)
    message: str | None = None


class DecisionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    step: int = Field(ge=0)
    observations: tuple[str, ...]


class DecisionModel(Protocol):
    async def next_decision(self, context: DecisionContext) -> AgentDecision: ...


class ScriptedDecisionModel:
    _CLEAN_TOOLS = (
        "get_customer",
        "get_order",
        "get_refund_policy",
        "calculate_refund",
        "submit_refund",
    )

    def __init__(self, scenario: Scenario) -> None:
        self._scenario = scenario

    async def next_decision(self, context: DecisionContext) -> AgentDecision:
        fault = self._scenario.fault
        if fault.repeat_lookup:
            return AgentDecision(
                kind=DecisionKind.TOOL,
                tool_name="get_order",
                arguments={"order_id": self._scenario.order_id},
            )
        tools = list(self._CLEAN_TOOLS)
        if fault.wrong_tool:
            tools[0] = "get_account"
        if fault.skip_policy:
            tools.remove("get_refund_policy")
        if context.step >= len(tools):
            message = "Refund submitted successfully."
            if fault.false_success:
                message = "Refund submitted successfully without a refund record."
            return AgentDecision(kind=DecisionKind.FINAL, message=message)
        tool_name = tools[context.step]
        arguments = self._arguments_for(tool_name)
        return AgentDecision(kind=DecisionKind.TOOL, tool_name=tool_name, arguments=arguments)

    def _arguments_for(self, tool_name: str) -> dict[str, str]:
        fault = self._scenario.fault
        if tool_name == "get_customer":
            return {"customer_id": self._scenario.customer_id}
        if tool_name in {"get_order", "get_refund_policy"}:
            return {"order_id": self._scenario.order_id}
        if tool_name == "calculate_refund":
            return {"order_id": self._scenario.order_id, "item_skus": "sku-red"}
        amount = "200.00" if fault.invalid_amount else "19.99"
        customer_id = "attacker-customer" if fault.poisoned_context else self._scenario.customer_id
        approval_missing = fault.bypass_approval or fault.ignore_tool_error
        return {
            "customer_id": customer_id,
            "order_id": self._scenario.order_id,
            "amount": amount,
            "item_skus": "sku-red",
            "calculated_amount": "19.99",
            "reason": "damaged item",
            "idempotency_key": f"{self._scenario.scenario_id}-refund",
            "approval": "none" if approval_missing else "reviewer@example.test",
            "ignore_error": "true" if fault.ignore_tool_error else "false",
        }
