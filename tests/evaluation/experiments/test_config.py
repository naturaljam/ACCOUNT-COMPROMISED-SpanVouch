from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from spanvouch.evaluation.experiments.config import (
    ConditionId,
    ExperimentMode,
    FormalFreezePolicy,
    Phase5ExperimentConfig,
    freeze_formal_config,
    load_experiment_config,
)


def test_checked_in_pilot_configuration_is_complete() -> None:
    config = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    assert config.mode is ExperimentMode.PILOT
    assert config.repetitions == 3
    assert config.conditions == tuple(ConditionId)
    assert config.generator.provider == "deepseek"
    assert config.cross_model_verifier.model == "Qwen/Qwen3-14B"
    assert config.budget.monthly_cap_cny == Decimal("1000")
    assert config.budget.pilot_fraction == Decimal("0.10")
    assert config.budget.stop_fraction == Decimal("0.80")


def test_formal_config_rejects_unfrozen_primary_fields() -> None:
    payload = load_experiment_config(
        Path("evals/configs/phase5-pilot.json")
    ).model_dump(mode="json")
    payload.update(mode="formal", repetitions=5, frozen_at_utc=None)
    with pytest.raises(ValueError, match="formal configuration must be frozen"):
        Phase5ExperimentConfig.model_validate(payload)


def test_freeze_formal_config_emits_a_self_hashed_configuration() -> None:
    pilot = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    policy = FormalFreezePolicy.model_validate_json(
        Path("evals/configs/phase5-formal-policy.json").read_text(encoding="utf-8")
    )

    formal = freeze_formal_config(
        pilot,
        policy,
        repetitions=policy.minimum_repetitions,
        coverage_loss_tolerance=0.05,
        frozen_at_utc=datetime(2026, 7, 19, tzinfo=UTC),
    )

    assert formal.mode is ExperimentMode.FORMAL
    assert formal.repetitions == 5
    assert formal.coverage_loss_tolerance == 0.05
    assert formal.config_sha256 is not None


def test_frozen_formal_endpoint_options_cannot_be_mutated() -> None:
    pilot = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    policy = FormalFreezePolicy.model_validate_json(
        Path("evals/configs/phase5-formal-policy.json").read_text(encoding="utf-8")
    )
    formal = freeze_formal_config(
        pilot,
        policy,
        repetitions=policy.minimum_repetitions,
        coverage_loss_tolerance=0.05,
        frozen_at_utc=datetime(2026, 7, 19, tzinfo=UTC),
    )

    thinking_options = cast(
        dict[str, JsonValue],
        formal.cross_model_verifier.extra_body["chat_template_kwargs"],
    )
    with pytest.raises(TypeError, match="immutable"):
        thinking_options["enable_thinking"] = True
