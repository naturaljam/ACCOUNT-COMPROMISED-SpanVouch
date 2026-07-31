import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from spanvouch.contracts.versioning import canonical_bytes
from spanvouch.evaluation.experiments.budget import Pricing
from spanvouch.evaluation.experiments.config import (
    ConditionId,
    ExperimentMode,
    FormalFreezePolicy,
    Phase5ExperimentConfig,
    _freeze_json_value,
    _FrozenJsonDict,
    _FrozenJsonList,
    freeze_formal_config,
    load_experiment_config,
)


def test_freeze_json_value_preserves_scalar_identity() -> None:
    assert _freeze_json_value("stable") == "stable"
    assert _freeze_json_value(["stable"]) == ["stable"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__("new", 1),
        lambda value: value.__delitem__("item"),
        lambda value: value.__ior__({"new": 1}),
        lambda value: value.clear(),
        lambda value: value.pop("item"),
        lambda value: value.popitem(),
        lambda value: value.setdefault("new", 1),
        lambda value: value.update({"new": 1}),
    ],
)
def test_frozen_json_dictionary_rejects_every_public_mutator(mutate: object) -> None:
    value = _FrozenJsonDict({"item": 1})
    with pytest.raises(TypeError, match="immutable"):
        cast(object, mutate)(value)  # type: ignore[operator]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__(0, 2),
        lambda value: value.__delitem__(0),
        lambda value: value.__iadd__([2]),
        lambda value: value.__imul__(2),
        lambda value: value.append(2),
        lambda value: value.clear(),
        lambda value: value.extend([2]),
        lambda value: value.insert(0, 2),
        lambda value: value.pop(),
        lambda value: value.remove(1),
        lambda value: value.reverse(),
        lambda value: value.sort(),
    ],
)
def test_frozen_json_list_rejects_every_public_mutator(mutate: object) -> None:
    value = _FrozenJsonList([1])
    with pytest.raises(TypeError, match="immutable"):
        cast(object, mutate)(value)  # type: ignore[operator]


def test_checked_in_pilot_configuration_is_complete() -> None:
    config = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    assert config.mode is ExperimentMode.PILOT
    assert config.repetitions == 3
    assert config.conditions == tuple(ConditionId)
    assert config.generator.provider == "deepseek"
    assert config.cross_model_verifier.model == "qwen3.7-plus"
    assert config.cross_model_verifier.extra_body == {"enable_thinking": False}
    assert config.budget.monthly_cap_cny == Decimal("1000")
    assert config.budget.pilot_fraction == Decimal("0.10")
    assert config.budget.stop_fraction == Decimal("0.80")
    assert config.live_provenance.deepseek.provider == "deepseek"
    assert config.live_provenance.deepseek.service_operator == "deepseek"
    assert config.live_provenance.qwen.provider == "qwen"
    assert config.live_provenance.qwen.service_operator == "alibaba-cloud-model-studio"
    assert config.live_provenance.qwen.deployment_type == "managed-api"
    assert config.live_provenance.qwen.api_compatibility == "openai-compatible"
    assert config.live_provenance.deepseek.pricing.currency == "USD"
    assert config.live_provenance.qwen.pricing.currency == "CNY"


def test_checked_in_pricing_files_match_pilot_provenance() -> None:
    config = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    checked_in = (
        (
            Path("evals/configs/phase5-deepseek-v4-flash-pricing.json"),
            config.live_provenance.deepseek,
        ),
        (
            Path("evals/configs/phase5-qwen3.7-plus-pricing.json"),
            config.live_provenance.qwen,
        ),
    )

    for path, provenance in checked_in:
        content = path.read_bytes()
        payload = json.loads(content)
        assert content == canonical_bytes(payload)
        pricing = Pricing.model_validate(payload)
        assert sha256(content).hexdigest() == provenance.pricing.canonical_sha256
        assert pricing.provider == provenance.provider
        assert pricing.model == provenance.model
        assert pricing.source_url == provenance.pricing.source_url
        assert pricing.effective_date == provenance.pricing.effective_date
        assert pricing.currency == provenance.pricing.currency


def test_pilot_and_formal_configs_reject_missing_live_provenance() -> None:
    payload = load_experiment_config(
        Path("evals/configs/phase5-pilot.json")
    ).model_dump(mode="json")
    payload.pop("live_provenance")
    with pytest.raises(ValueError, match="live_provenance"):
        Phase5ExperimentConfig.model_validate(payload)


def test_managed_live_provenance_rejects_missing_or_legacy_identity() -> None:
    payload = load_experiment_config(
        Path("evals/configs/phase5-pilot.json")
    ).model_dump(mode="json")
    qwen = payload["live_provenance"]["qwen"]
    for field in ("service_operator", "deployment_type", "api_compatibility"):
        changed = {**payload, "live_provenance": {**payload["live_provenance"]}}
        changed["live_provenance"]["qwen"] = {
            key: value for key, value in qwen.items() if key != field
        }
        with pytest.raises(ValueError, match=field):
            Phase5ExperimentConfig.model_validate(changed)

    for legacy_field in ("container_repo_digest", "hf_revision", "gpu_lease_approval"):
        changed = {**payload, "live_provenance": {**payload["live_provenance"]}}
        changed["live_provenance"]["qwen"] = {**qwen, legacy_field: "legacy"}
        with pytest.raises(ValueError, match="extra"):
            Phase5ExperimentConfig.model_validate(changed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("service_operator", "deepseek"),
        ("deployment_type", "self-hosted-vllm"),
        ("api_compatibility", "native"),
    ],
)
def test_qwen_live_provenance_rejects_wrong_managed_role(field: str, value: str) -> None:
    payload = load_experiment_config(
        Path("evals/configs/phase5-pilot.json")
    ).model_dump(mode="json")
    qwen = payload["live_provenance"]["qwen"]
    changed = {**payload, "live_provenance": {**payload["live_provenance"]}}
    changed["live_provenance"]["qwen"] = {**qwen, field: value}
    with pytest.raises(ValueError, match="managed|operator|compatibility|extra"):
        Phase5ExperimentConfig.model_validate(changed)


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

    thinking_options = cast(dict[str, JsonValue], formal.cross_model_verifier.extra_body)
    with pytest.raises(TypeError, match="immutable"):
        thinking_options["enable_thinking"] = True


def test_deepseek_endpoints_require_frozen_non_thinking_mode() -> None:
    pilot = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    disabled = {"thinking": {"type": "disabled"}}
    assert pilot.generator.extra_body == disabled
    assert pilot.shared_verifier.extra_body == disabled
    assert pilot.isolated_verifier.extra_body == disabled

    payload = pilot.model_dump(mode="json")
    for endpoint_name in ("generator", "shared_verifier", "isolated_verifier"):
        changed = {**payload, endpoint_name: {**payload[endpoint_name], "extra_body": {}}}
        with pytest.raises(ValueError, match="DeepSeek thinking"):
            Phase5ExperimentConfig.model_validate(changed)


def test_config_and_freeze_policy_reject_every_preregistered_drift() -> None:
    pilot = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    payload = pilot.model_dump(mode="json")
    invalid = (
        ({**payload, "conditions": list(ConditionId)[:-1]}, "six conditions"),
        ({**payload, "frameworks": ["langgraph", "langgraph"]}, "both frameworks"),
        ({**payload, "repetitions": 4}, "exactly three"),
        ({**payload, "coverage_loss_tolerance": 0.01}, "must not set"),
        ({**payload, "frozen_at_utc": "2026-07-20T00:00:00Z"}, "must not be frozen"),
    )
    for changed, message in invalid:
        with pytest.raises(ValueError, match=message):
            Phase5ExperimentConfig.model_validate(changed)

    with pytest.raises(ValueError, match="at least five"):
        Phase5ExperimentConfig.model_validate(
            {
                **payload,
                "mode": "formal",
                "repetitions": 4,
                "coverage_loss_tolerance": 0.05,
                "frozen_at_utc": "2026-07-20T00:00:00Z",
                "config_sha256": "f" * 64,
            }
        )

    policy_payload = {
        "minimum_repetitions": 6,
        "maximum_repetitions": 5,
        "maximum_coverage_loss": 0.1,
        "required_confidence_level": 0.95,
        "bootstrap_draws": 10,
        "multiple_comparison_correction": "holm",
    }
    with pytest.raises(ValueError, match="minimum_repetitions"):
        FormalFreezePolicy.model_validate(policy_payload)


def test_formal_freeze_rejects_invalid_source_bounds_loss_and_time() -> None:
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
    with pytest.raises(ValueError, match="only a pilot"):
        freeze_formal_config(
            formal, policy, repetitions=5, coverage_loss_tolerance=0.05,
            frozen_at_utc=datetime(2026, 7, 19, tzinfo=UTC)
        )
    with pytest.raises(ValueError, match="repetitions"):
        freeze_formal_config(
            pilot, policy, repetitions=4, coverage_loss_tolerance=0.05,
            frozen_at_utc=datetime(2026, 7, 19, tzinfo=UTC)
        )
    with pytest.raises(ValueError, match="coverage_loss_tolerance"):
        freeze_formal_config(
            pilot, policy, repetitions=5, coverage_loss_tolerance=0.2,
            frozen_at_utc=datetime(2026, 7, 19, tzinfo=UTC)
        )
    with pytest.raises(ValueError, match="must be UTC"):
        freeze_formal_config(
            pilot, policy, repetitions=5, coverage_loss_tolerance=0.05,
            frozen_at_utc=datetime(2026, 7, 19)
        )
