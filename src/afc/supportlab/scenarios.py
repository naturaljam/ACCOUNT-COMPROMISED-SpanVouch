from random import Random

from pydantic import BaseModel, ConfigDict

from afc.failure_types import FailureType


class FaultProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    wrong_tool: bool = False
    invalid_amount: bool = False
    skip_policy: bool = False
    ignore_tool_error: bool = False
    poisoned_context: bool = False
    bypass_approval: bool = False
    repeat_lookup: bool = False
    false_success: bool = False


class Scenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scenario_id: str
    user_request: str
    customer_id: str = "cust-001"
    order_id: str = "order-001"
    expected_failure: FailureType
    expected_critical_operation: str
    fault: FaultProfile


_FAULTS: dict[FailureType, tuple[str, FaultProfile]] = {
    FailureType.WRONG_TOOL: ("get_account", FaultProfile(wrong_tool=True)),
    FailureType.INVALID_ARGUMENT: ("submit_refund", FaultProfile(invalid_amount=True)),
    FailureType.MISSING_PRECONDITION: ("get_refund_policy", FaultProfile(skip_policy=True)),
    FailureType.IGNORED_TOOL_ERROR: ("submit_refund", FaultProfile(ignore_tool_error=True)),
    FailureType.CONTEXT_CORRUPTION: ("submit_refund", FaultProfile(poisoned_context=True)),
    FailureType.POLICY_VIOLATION: ("submit_refund", FaultProfile(bypass_approval=True)),
    FailureType.LOOP_OR_BUDGET_EXHAUSTION: ("get_order", FaultProfile(repeat_lookup=True)),
    FailureType.INVALID_FINAL_STATE: ("finalize", FaultProfile(false_success=True)),
}


def build_scenarios(seed: int = 20260715) -> tuple[Scenario, ...]:
    scenarios = [
        Scenario(
            scenario_id=f"clean-{index:02d}",
            user_request="Refund the damaged red item from order-001.",
            expected_failure=FailureType.NO_FAILURE,
            expected_critical_operation="none",
            fault=FaultProfile(),
        )
        for index in range(1, 5)
    ]
    for failure_type, (operation, profile) in _FAULTS.items():
        for index in range(1, 3):
            scenarios.append(
                Scenario(
                    scenario_id=f"{failure_type.value}-{index:02d}",
                    user_request="Refund the damaged red item from order-001.",
                    expected_failure=failure_type,
                    expected_critical_operation=operation,
                    fault=profile,
                )
            )
    random = Random(seed)
    random.shuffle(scenarios)
    return tuple(scenarios)
