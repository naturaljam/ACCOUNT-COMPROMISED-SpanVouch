from pathlib import Path

import pytest

from spanvouch.labs.runtime import (
    AgentAction,
    ExecutionStatus,
    LabScenario,
    RuntimeFailure,
    RuntimeFailureCategory,
    RuntimeState,
)
from spanvouch.labs.supportlab.decision import (
    AgentDecision,
    DecisionContext,
    DecisionKind,
)
from spanvouch.labs.supportlab.environment import (
    FrameworkIncompatibilityError,
    SupportLabEnvironment,
    SupportLabEnvironmentRegistry,
)
from spanvouch.labs.supportlab.repository import build_seed_repository
from spanvouch.labs.supportlab.runtime import support_scenario_to_lab
from spanvouch.labs.supportlab.scenarios import build_scenarios
from spanvouch.labs.supportlab.tools import SupportTools


class FixedDecisionModel:
    def __init__(self, decision: AgentDecision) -> None:
        self._decision = decision

    async def next_decision(self, context: DecisionContext) -> AgentDecision:
        return self._decision


def _scenario(scenario_id: str) -> LabScenario:
    historical = next(
        item for item in build_scenarios() if item.scenario_id == scenario_id
    )
    return support_scenario_to_lab(historical)


@pytest.mark.asyncio
async def test_environment_runs_without_importing_a_framework() -> None:
    environment = SupportLabEnvironmentRegistry().build(_scenario("clean-01"))
    state = RuntimeState.initial()

    while environment.terminal_status(state) is None:
        action = await environment.decide(state)
        if action.kind == "final":
            state = state.with_final(action.final_message or "")
        else:
            observation = await environment.execute(action)
            state = state.with_observation(observation)

    assert environment.terminal_status(state) is ExecutionStatus.SUCCEEDED
    assert state.tool_calls == 5
    assert tuple(item.tool_name for item in state.observations) == (
        "get_customer",
        "get_order",
        "get_refund_policy",
        "calculate_refund",
        "submit_refund",
    )


@pytest.mark.asyncio
async def test_execute_converts_decimal_approval_and_sanitizes_success() -> None:
    repository = build_seed_repository()
    environment = SupportLabEnvironment(
        scenario=_scenario("clean-01"),
        tools=SupportTools(repository),
    )

    observation = await environment.execute(
        AgentAction(
            kind="tool",
            tool_name="submit_refund",
            arguments={
                "customer_id": "cust-001",
                "order_id": "order-001",
                "amount": "19.99",
                "item_skus": "sku-red",
                "reason": "damaged item",
                "idempotency_key": "environment-refund",
                "approval": "reviewer@example.test",
                "ignore_error": "false",
            },
        )
    )

    assert observation.status == "ok"
    assert observation.retryable is False
    assert isinstance(observation.result, str)
    assert "amount=Decimal('19.99')" in observation.result
    refunds = await repository.list_refunds("order-001")
    assert refunds[0].approved_by == "reviewer@example.test"


@pytest.mark.asyncio
async def test_hard_tool_error_is_sanitized_and_terminal() -> None:
    environment = SupportLabEnvironmentRegistry().build(_scenario("wrong_tool-01"))
    state = RuntimeState.initial()

    action = await environment.decide(state)
    observation = await environment.execute(action)
    state = state.with_observation(observation)

    assert observation.status == "error"
    assert observation.retryable is False
    assert observation.error == {
        "type": "KeyError",
        "message": "'unknown tool: get_account'",
    }
    assert environment.terminal_status(state) is ExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_tool_observation_sanitizes_adversarial_tool_names() -> None:
    environment = SupportLabEnvironmentRegistry().build(_scenario("clean-01"))
    secret = "sk-1234567890abcdef"

    observation = await environment.execute(
        AgentAction(
            kind="tool",
            tool_name=f"unknown api_key={secret}",
            arguments={},
        )
    )

    assert secret not in observation.model_dump_json()
    assert observation.tool_name == "unknown api_key=[REDACTED]"


@pytest.mark.asyncio
async def test_ignored_tool_error_remains_nonterminal() -> None:
    environment = SupportLabEnvironmentRegistry().build(
        _scenario("ignored_tool_error-01")
    )
    state = RuntimeState.initial()

    for _ in range(5):
        action = await environment.decide(state)
        observation = await environment.execute(action)
        state = state.with_observation(observation)

    assert state.observations[-1].status == "error"
    assert state.observations[-1].retryable is True
    assert environment.terminal_status(state) is None
    final = await environment.decide(state)
    assert final == AgentAction(
        kind="final", final_message="Refund submitted successfully."
    )


def test_terminal_status_maps_final_and_step_limit() -> None:
    environment = SupportLabEnvironmentRegistry().build(_scenario("clean-01"))
    state = RuntimeState.initial()

    assert environment.terminal_status(state.with_final("complete")) is (
        ExecutionStatus.SUCCEEDED
    )

    loop_environment = SupportLabEnvironmentRegistry().build(
        _scenario("loop_or_budget_exhaustion-01")
    )
    loop_state = RuntimeState.model_validate(
        {
            "step": 8,
            "tool_calls": 0,
            "observations": (),
            "final_message": None,
            "failure": None,
        }
    )
    assert loop_environment.terminal_status(loop_state) is ExecutionStatus.STEP_LIMIT


@pytest.mark.parametrize(
    ("category", "status"),
    [
        (RuntimeFailureCategory.FRAMEWORK_EXECUTION, ExecutionStatus.FAILED),
        (
            RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY,
            ExecutionStatus.INCOMPATIBLE,
        ),
    ],
)
def test_terminal_status_maps_typed_runtime_failures(
    category: RuntimeFailureCategory, status: ExecutionStatus
) -> None:
    environment = SupportLabEnvironmentRegistry().build(_scenario("clean-01"))
    failure = RuntimeFailure.from_message(
        category=category,
        code="test_failure",
        retryable=False,
        sanitized_message="test failure",
    )

    assert environment.terminal_status(RuntimeState.initial().with_failure(failure)) is status


@pytest.mark.parametrize(
    ("decision", "message"),
    [
        (AgentDecision(kind=DecisionKind.FINAL), "final decision requires a message"),
        (AgentDecision(kind=DecisionKind.TOOL), "tool decision requires a tool name"),
    ],
)
@pytest.mark.asyncio
async def test_decide_rejects_malformed_domain_decisions(
    decision: AgentDecision, message: str
) -> None:
    environment = SupportLabEnvironment(
        scenario=_scenario("clean-01"),
        tools=SupportTools(build_seed_repository()),
        decision_model=FixedDecisionModel(decision),
    )

    with pytest.raises(ValueError, match=message):
        await environment.decide(RuntimeState.initial())


@pytest.mark.asyncio
async def test_execute_rejects_final_actions() -> None:
    environment = SupportLabEnvironmentRegistry().build(_scenario("clean-01"))

    with pytest.raises(ValueError, match="execute only tool actions"):
        await environment.execute(AgentAction(kind="final", final_message="complete"))


def test_environment_rejects_nonpositive_step_limit() -> None:
    with pytest.raises(ValueError, match="max_steps must be positive"):
        SupportLabEnvironment(
            scenario=_scenario("clean-01"),
            tools=SupportTools(build_seed_repository()),
            max_steps=0,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("parameters", {"customer_id": 7, "order_id": "order-001"}, "parameter"),
        (
            "injection",
            {**_scenario("clean-01").injection, "wrong_tool": "yes"},
            "injection",
        ),
    ],
)
def test_environment_rejects_noncanonical_scenario_inputs(
    field: str, value: object, message: str
) -> None:
    scenario = _scenario("clean-01").model_copy(update={field: value})

    with pytest.raises(ValueError, match=message):
        SupportLabEnvironmentRegistry().build(scenario)


@pytest.mark.asyncio
async def test_execute_normalizes_nonstring_tool_arguments() -> None:
    environment = SupportLabEnvironmentRegistry().build(_scenario("clean-01"))

    observation = await environment.execute(
        AgentAction(kind="tool", tool_name="get_order", arguments={"order_id": 7})
    )

    assert observation.error == {
        "type": "ValueError",
        "message": "SupportLab argument order_id must be a string",
    }


@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        (
            LabScenario(
                **{
                    **_scenario("clean-01").model_dump(mode="python"),
                    "domain": "opslab",
                }
            ),
            "unsupported_domain",
        ),
        (
            LabScenario(
                **{
                    **_scenario("clean-01").model_dump(mode="python"),
                    "scenario_id": "unknown-support-scenario",
                }
            ),
            "unsupported_scenario",
        ),
    ],
)
def test_registry_rejects_incompatible_scenarios_with_typed_failure(
    scenario: LabScenario, code: str
) -> None:
    with pytest.raises(FrameworkIncompatibilityError) as raised:
        SupportLabEnvironmentRegistry().build(scenario)

    assert raised.value.failure.category is (
        RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY
    )
    assert raised.value.failure.code == code
    assert raised.value.failure.retryable is False


def test_environment_module_has_no_agent_framework_dependency() -> None:
    from spanvouch.labs.supportlab import environment as environment_module

    source = Path(environment_module.__file__).read_text(encoding="utf-8")

    assert "langgraph" not in source
    assert "autogen_agentchat" not in source
