from __future__ import annotations

import json

import pytest

from spanvouch.labs.frameworks.autogen import AutoGenRuntimeAdapter
from spanvouch.labs.frameworks.langgraph import LangGraphRuntimeAdapter
from spanvouch.labs.opslab import build_opslab_templates
from spanvouch.labs.opslab.environment import OpsLabEnvironmentRegistry
from spanvouch.labs.registry import CombinedLabEnvironmentRegistry
from spanvouch.labs.runtime import (
    ExecutionProvenance,
    ExecutionRecord,
    ExecutionStatus,
    LabScenario,
    RuntimeConfig,
    RuntimeFailureCategory,
    ScenarioParityValidator,
)
from spanvouch.labs.supportlab.environment import SupportLabEnvironmentRegistry
from spanvouch.labs.supportlab.runtime import build_support_lab_scenarios


def _provenance() -> ExecutionProvenance:
    return ExecutionProvenance(
        git_commit="b" * 40,
        package_version="0.2.0",
        dependency_lock_sha256="c" * 64,
        dataset_manifest_sha256="d" * 64,
        environment_sha256="e" * 64,
        tool_versions={"opslab": "1"},
        runtime_versions={"python": "3.12"},
        dirty_worktree=False,
    )


def _config() -> RuntimeConfig:
    return RuntimeConfig(
        seed=20260719,
        repetition=1,
        max_steps=12,
        timeout_seconds=5.0,
        max_retries=0,
        max_tool_calls=12,
    )


def _assert_safe_injection_marker(
    record: ExecutionRecord,
    scenario: LabScenario,
) -> None:
    markers = [
        span.attributes
        for span in record.trace.spans
        if "injection.trigger.id" in span.attributes
    ]
    if not scenario.injection:
        assert record.injection_trigger_id == "none"
        assert markers == []
        return
    assert record.injection_trigger_id != "unobserved"
    assert record.injection_trigger_sha256 == scenario.injection_trigger_digest(
        record.injection_trigger_id
    )
    assert markers == [
        {
            "injection.trigger.id": record.injection_trigger_id,
            "injection.trigger.sha256": record.injection_trigger_sha256,
        }
    ]


def _assert_unbounded_retry_budget(
    record: ExecutionRecord,
    config: RuntimeConfig,
) -> None:
    upstream_calls = [
        span for span in record.trace.spans if span.name == "call-upstream"
    ]
    assert record.status is ExecutionStatus.STEP_LIMIT
    assert len(upstream_calls) == config.max_steps - 1
    payload = json.loads(upstream_calls[-1].attributes["tool.error.message"])
    assert payload["attempts"] == config.max_steps - 1
    assert payload["upstream_calls"] == config.max_steps - 1
    assert payload["backoff"] == config.max_steps - 1
    assert payload["retry_policy"] == "unbounded"


def test_combined_registry_delegates_domains_and_types_unknown_domain() -> None:
    registry = CombinedLabEnvironmentRegistry(
        supportlab=SupportLabEnvironmentRegistry(),
        opslab=OpsLabEnvironmentRegistry(),
    )
    support = build_support_lab_scenarios()[0]
    ops = build_opslab_templates()[0].to_lab_scenario()
    invalid = ops.model_copy(update={"domain": "unknown"})

    assert registry.build(support).scenario == support
    assert registry.build(ops).scenario == ops
    with pytest.raises(RuntimeError) as caught:
        registry.build(invalid)
    assert caught.value.failure.category is RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY
    assert caught.value.failure.code == "unsupported_domain"


@pytest.mark.parametrize(
    "change",
    [
        {"failure_family": "unknown-family"},
        {"user_request": "Altered request."},
        {"parameters": {"operation_plan": []}},
        {
            "injection": {
                "fault_code": "unknown-fault",
                "operation": "call-upstream",
                "attempt": 1,
            }
        },
        {
            "injection": {
                "fault_code": "timeout-no-retry",
                "operation": "call-upstream",
                "attempt": 2,
            }
        },
    ],
)
def test_opslab_registry_types_every_tampered_known_scenario(
    change: dict[str, object],
) -> None:
    registry = OpsLabEnvironmentRegistry()
    scenario = next(
        item.to_lab_scenario()
        for item in build_opslab_templates()
        if item.template_id == "timeout-no-retry"
    )

    with pytest.raises(RuntimeError) as caught:
        registry.build(scenario.model_copy(update=change))

    assert caught.value.failure.category is RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY
    assert caught.value.failure.code == "scenario_mismatch"


@pytest.mark.asyncio
async def test_all_sixteen_templates_match_across_both_frameworks() -> None:
    registry = CombinedLabEnvironmentRegistry(
        supportlab=SupportLabEnvironmentRegistry(),
        opslab=OpsLabEnvironmentRegistry(),
    )
    langgraph = LangGraphRuntimeAdapter(registry, provenance=_provenance())
    autogen = AutoGenRuntimeAdapter(registry, provenance=_provenance())
    validator = ScenarioParityValidator()
    config = _config()

    for template in build_opslab_templates():
        scenario = template.to_lab_scenario()
        left = await langgraph.execute(scenario, config)
        right = await autogen.execute(scenario, config)
        assert validator.validate(left, right).is_match, template.template_id
        assert left.status is right.status
        _assert_safe_injection_marker(left, scenario)
        _assert_safe_injection_marker(right, scenario)
        if template.injection is None:
            assert left.status is ExecutionStatus.SUCCEEDED
        else:
            assert left.status in {ExecutionStatus.FAILED, ExecutionStatus.STEP_LIMIT}
        if template.template_id == "timeout-unbounded-retry":
            _assert_unbounded_retry_budget(left, config)
            _assert_unbounded_retry_budget(right, config)
