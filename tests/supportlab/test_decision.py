import pytest

from afc.supportlab.decision import DecisionContext, DecisionKind, ScriptedDecisionModel
from afc.supportlab.scenarios import FailureType, Scenario, build_scenarios


def scenario_for(failure_type: FailureType) -> Scenario:
    return next(item for item in build_scenarios() if item.expected_failure is failure_type)


@pytest.mark.asyncio
async def test_clean_script_checks_order_policy_amount_then_refunds() -> None:
    model = ScriptedDecisionModel(scenario_for(FailureType.NO_FAILURE))

    names = []
    for step in range(6):
        decision = await model.next_decision(DecisionContext(step=step, observations=()))
        names.append(decision.tool_name or decision.kind.value)

    assert names == [
        "get_customer",
        "get_order",
        "get_refund_policy",
        "calculate_refund",
        "submit_refund",
        DecisionKind.FINAL.value,
    ]


@pytest.mark.asyncio
async def test_loop_fault_repeats_until_budget_boundary() -> None:
    model = ScriptedDecisionModel(scenario_for(FailureType.LOOP_OR_BUDGET_EXHAUSTION))

    decisions = [
        await model.next_decision(DecisionContext(step=step, observations=()))
        for step in range(8)
    ]

    assert all(item.tool_name == "get_order" for item in decisions)
