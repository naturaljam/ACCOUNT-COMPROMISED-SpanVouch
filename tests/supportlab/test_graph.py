import pytest

from afc.observability.tracing import build_test_tracer
from afc.supportlab.decision import ScriptedDecisionModel
from afc.supportlab.graph import RunOutcome, run_support_scenario
from afc.supportlab.repository import build_seed_repository
from afc.supportlab.scenarios import FailureType, Scenario, build_scenarios
from afc.supportlab.tools import SupportTools


def scenario_for(failure_type: FailureType) -> Scenario:
    return next(item for item in build_scenarios() if item.expected_failure is failure_type)


@pytest.mark.asyncio
async def test_clean_scenario_creates_one_refund() -> None:
    scenario = scenario_for(FailureType.NO_FAILURE)
    repository = build_seed_repository()
    tracer, exporter = build_test_tracer()

    result = await run_support_scenario(
        scenario=scenario,
        tools=SupportTools(repository),
        decision_model=ScriptedDecisionModel(scenario),
        tracer=tracer,
    )

    assert result.outcome is RunOutcome.SUCCEEDED
    assert len(await repository.list_refunds("order-001")) == 1
    finished_spans = exporter.get_finished_spans()
    run_span = next(span for span in finished_spans if span.name == "supportlab.run")
    refund_span = next(span for span in finished_spans if span.name == "submit_refund")
    assert run_span.attributes is not None
    assert refund_span.attributes is not None
    assert run_span.attributes["run.outcome"] == RunOutcome.SUCCEEDED.value
    assert refund_span.attributes["tool.name"] == "submit_refund"
    assert "tool.result" in refund_span.attributes


@pytest.mark.asyncio
async def test_loop_scenario_stops_at_max_steps() -> None:
    scenario = scenario_for(FailureType.LOOP_OR_BUDGET_EXHAUSTION)
    tracer, _ = build_test_tracer()

    result = await run_support_scenario(
        scenario=scenario,
        tools=SupportTools(build_seed_repository()),
        decision_model=ScriptedDecisionModel(scenario),
        tracer=tracer,
        max_steps=4,
    )

    assert result.outcome is RunOutcome.STEP_LIMIT
    assert result.steps == 4
