from collections import Counter

from spanvouch.labs.supportlab.scenarios import FailureType, Scenario, build_scenarios


def test_scenario_matrix_has_stable_size_and_distribution() -> None:
    scenarios: tuple[Scenario, ...] = build_scenarios()
    counts = Counter(item.expected_failure for item in scenarios)

    assert len(scenarios) == 20
    assert len({item.scenario_id for item in scenarios}) == 20
    assert counts[FailureType.NO_FAILURE] == 4
    for failure_type in set(FailureType) - {FailureType.NO_FAILURE}:
        assert counts[failure_type] == 2


def test_scenario_generation_is_deterministic() -> None:
    first = [item.model_dump(mode="json") for item in build_scenarios(seed=7)]
    second = [item.model_dump(mode="json") for item in build_scenarios(seed=7)]

    assert first == second
