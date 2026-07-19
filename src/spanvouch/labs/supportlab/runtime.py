from __future__ import annotations

from collections.abc import Mapping
from random import Random
from typing import Protocol

from pydantic import JsonValue

from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.labs.runtime import LabScenario

_DEFAULT_USER_REQUEST = "Refund the damaged red item from order-001."
_TERMINAL_PREDICATE_ID = "supportlab-terminal-v1"
_ALLOWED_EVIDENCE_SELECTORS = (
    "tool.name",
    "tool.arguments.customer_id",
    "tool.arguments.order_id",
    "tool.arguments.item_skus",
    "tool.arguments.amount",
    "tool.arguments.approval",
    "tool.arguments.reason",
    "tool.result",
    "tool.error.type",
    "tool.error.message",
    "run.outcome",
    "run.final_message",
)
_FAULT_NAMES = (
    "wrong_tool",
    "invalid_amount",
    "skip_policy",
    "ignore_tool_error",
    "poisoned_context",
    "bypass_approval",
    "repeat_lookup",
    "false_success",
)
_FAMILY_TO_FAULT = {
    "wrong_tool": "wrong_tool",
    "invalid_argument": "invalid_amount",
    "missing_precondition": "skip_policy",
    "ignored_tool_error": "ignore_tool_error",
    "context_corruption": "poisoned_context",
    "policy_violation": "bypass_approval",
    "loop_or_budget_exhaustion": "repeat_lookup",
    "invalid_final_state": "false_success",
}

SUPPORT_TOOL_CONTRACT: JsonValue = {
    "calculate_refund": ["item_skus", "order_id"],
    "get_customer": ["customer_id"],
    "get_order": ["order_id"],
    "get_refund_policy": ["order_id"],
    "submit_refund": [
        "amount",
        "approval",
        "customer_id",
        "idempotency_key",
        "item_skus",
        "order_id",
        "reason",
    ],
}


class _FaultInput(Protocol):
    @property
    def wrong_tool(self) -> bool: ...

    @property
    def invalid_amount(self) -> bool: ...

    @property
    def skip_policy(self) -> bool: ...

    @property
    def ignore_tool_error(self) -> bool: ...

    @property
    def poisoned_context(self) -> bool: ...

    @property
    def bypass_approval(self) -> bool: ...

    @property
    def repeat_lookup(self) -> bool: ...

    @property
    def false_success(self) -> bool: ...


class SupportScenarioInput(Protocol):
    @property
    def scenario_id(self) -> str: ...

    @property
    def user_request(self) -> str: ...

    @property
    def customer_id(self) -> str: ...

    @property
    def order_id(self) -> str: ...

    @property
    def fault(self) -> _FaultInput: ...


def _empty_injection() -> dict[str, JsonValue]:
    return {name: False for name in _FAULT_NAMES}


def _lab_scenario(
    *,
    scenario_id: str,
    failure_family: str,
    user_request: str,
    customer_id: str,
    order_id: str,
    injection: Mapping[str, JsonValue],
) -> LabScenario:
    return LabScenario(
        scenario_id=scenario_id,
        template_id=scenario_id,
        domain="supportlab",
        failure_family=failure_family,
        user_request=user_request,
        parameters={"customer_id": customer_id, "order_id": order_id},
        injection=dict(injection),
        tool_contract_sha256=canonical_sha256(SUPPORT_TOOL_CONTRACT),
        terminal_predicate_id=_TERMINAL_PREDICATE_ID,
        allowed_evidence_selectors=_ALLOWED_EVIDENCE_SELECTORS,
    )


def _failure_family(injection: Mapping[str, JsonValue]) -> str:
    enabled = tuple(name for name in _FAULT_NAMES if injection.get(name) is True)
    if not enabled:
        return "clean"
    if len(enabled) != 1:
        raise ValueError("SupportLab scenario must enable exactly one fault injection")
    fault_to_family = {fault: family for family, fault in _FAMILY_TO_FAULT.items()}
    return fault_to_family[enabled[0]]


def support_scenario_to_lab(scenario: SupportScenarioInput) -> LabScenario:
    injection: dict[str, JsonValue] = {
        name: getattr(scenario.fault, name) for name in _FAULT_NAMES
    }
    return _lab_scenario(
        scenario_id=scenario.scenario_id,
        failure_family=_failure_family(injection),
        user_request=scenario.user_request,
        customer_id=scenario.customer_id,
        order_id=scenario.order_id,
        injection=injection,
    )


def build_support_lab_scenarios(seed: int = 20260715) -> tuple[LabScenario, ...]:
    scenarios = [
        _lab_scenario(
            scenario_id=f"clean-{index:02d}",
            failure_family="clean",
            user_request=_DEFAULT_USER_REQUEST,
            customer_id="cust-001",
            order_id="order-001",
            injection=_empty_injection(),
        )
        for index in range(1, 5)
    ]
    for family, fault_name in _FAMILY_TO_FAULT.items():
        for index in range(1, 3):
            injection = _empty_injection()
            injection[fault_name] = True
            scenarios.append(
                _lab_scenario(
                    scenario_id=f"{family}-{index:02d}",
                    failure_family=family,
                    user_request=_DEFAULT_USER_REQUEST,
                    customer_id="cust-001",
                    order_id="order-001",
                    injection=injection,
                )
            )
    random = Random(seed)
    random.shuffle(scenarios)
    return tuple(scenarios)
