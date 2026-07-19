from collections import Counter
from inspect import getsource

import pytest

from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.failure_types import FailureType
from spanvouch.labs.runtime import AgentAction, ToolObservation
from spanvouch.labs.supportlab.decision import ScriptedDecisionModel
from spanvouch.labs.supportlab.environment import SupportLabEnvironment
from spanvouch.labs.supportlab.graph import RunOutcome, run_support_scenario
from spanvouch.labs.supportlab.repository import build_seed_repository
from spanvouch.labs.supportlab.runtime import (
    SUPPORT_TOOL_CONTRACT,
    build_support_lab_scenarios,
    support_scenario_to_lab,
)
from spanvouch.labs.supportlab.scenarios import FaultProfile, build_scenarios
from spanvouch.labs.supportlab.tools import SupportTools
from spanvouch.observability.tracing import build_test_tracer


def test_stage_a_builder_is_execution_only_and_matches_historical_ids() -> None:
    generated = build_support_lab_scenarios(seed=20260715)
    historical = build_scenarios(seed=20260715)
    serialized = [item.model_dump(mode="json") for item in generated]
    builder_source = getsource(build_support_lab_scenarios)

    assert tuple(item.scenario_id for item in generated) == tuple(
        item.scenario_id for item in historical
    )
    assert len(generated) == 20
    assert Counter(item.failure_family for item in generated) == {
        "clean": 4,
        "wrong_tool": 2,
        "invalid_argument": 2,
        "missing_precondition": 2,
        "ignored_tool_error": 2,
        "context_corruption": 2,
        "policy_violation": 2,
        "loop_or_budget_exhaustion": 2,
        "invalid_final_state": 2,
    }
    assert "build_scenarios" not in builder_source
    assert "expected_failure" not in builder_source
    assert "expected_critical_operation" not in builder_source
    assert all("expected_failure" not in str(item) for item in serialized)
    assert all("expected_critical_operation" not in str(item) for item in serialized)


def test_scenario_projection_contains_only_execution_inputs_and_injection() -> None:
    historical = next(
        item for item in build_scenarios() if item.scenario_id == "invalid_argument-01"
    )

    projected = support_scenario_to_lab(historical)

    assert projected.scenario_id == historical.scenario_id
    assert projected.domain == "supportlab"
    assert projected.parameters == {
        "customer_id": "cust-001",
        "order_id": "order-001",
    }
    assert projected.injection == historical.fault.model_dump(mode="json")
    assert projected.failure_family == "invalid_argument"
    assert projected.tool_contract_sha256 == canonical_sha256(SUPPORT_TOOL_CONTRACT)
    assert projected == next(
        item
        for item in build_support_lab_scenarios()
        if item.scenario_id == historical.scenario_id
    )


def test_scenario_projection_rejects_multiple_fault_injections() -> None:
    historical = build_scenarios()[0].model_copy(
        update={"fault": FaultProfile(wrong_tool=True, invalid_amount=True)}
    )

    with pytest.raises(ValueError, match="exactly one fault injection"):
        support_scenario_to_lab(historical)


@pytest.mark.asyncio
async def test_graph_compatibility_wrapper_delegates_tool_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = next(
        item for item in build_scenarios() if item.expected_failure is FailureType.NO_FAILURE
    )
    calls: list[str] = []
    original = SupportLabEnvironment.execute

    async def recording_execute(
        self: SupportLabEnvironment, action: AgentAction
    ) -> ToolObservation:
        assert action.tool_name is not None
        calls.append(action.tool_name)
        return await original(self, action)

    monkeypatch.setattr(SupportLabEnvironment, "execute", recording_execute)

    result = await run_support_scenario(
        scenario=scenario,
        tools=SupportTools(build_seed_repository()),
        decision_model=ScriptedDecisionModel(scenario),
        tracer=build_test_tracer()[0],
    )

    assert result.outcome is RunOutcome.SUCCEEDED
    assert calls == [
        "get_customer",
        "get_order",
        "get_refund_policy",
        "calculate_refund",
        "submit_refund",
    ]
