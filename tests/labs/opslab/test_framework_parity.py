from __future__ import annotations

import pytest

from spanvouch.labs.frameworks.autogen import AutoGenRuntimeAdapter
from spanvouch.labs.frameworks.langgraph import LangGraphRuntimeAdapter
from spanvouch.labs.opslab import build_opslab_templates
from spanvouch.labs.opslab.environment import OpsLabEnvironmentRegistry
from spanvouch.labs.registry import CombinedLabEnvironmentRegistry
from spanvouch.labs.runtime import (
    ExecutionProvenance,
    ExecutionStatus,
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


@pytest.mark.asyncio
async def test_all_sixteen_templates_match_across_both_frameworks() -> None:
    registry = CombinedLabEnvironmentRegistry(
        supportlab=SupportLabEnvironmentRegistry(),
        opslab=OpsLabEnvironmentRegistry(),
    )
    langgraph = LangGraphRuntimeAdapter(registry, provenance=_provenance())
    autogen = AutoGenRuntimeAdapter(registry, provenance=_provenance())
    validator = ScenarioParityValidator()

    for template in build_opslab_templates():
        scenario = template.to_lab_scenario()
        left = await langgraph.execute(scenario, _config())
        right = await autogen.execute(scenario, _config())
        assert validator.validate(left, right).is_match, template.template_id
        assert left.status is right.status
        if template.injection is None:
            assert left.status is ExecutionStatus.SUCCEEDED
            assert left.injection_trigger_id == "none"
            assert not any(
                "injection.trigger.id" in span.attributes for span in left.trace.spans
            )
        else:
            assert left.status in {ExecutionStatus.FAILED, ExecutionStatus.STEP_LIMIT}
            assert left.injection_trigger_id != "unobserved"
            markers = [
                span.attributes
                for span in left.trace.spans
                if "injection.trigger.id" in span.attributes
            ]
            assert len(markers) == 1
            assert markers[0] == {
                "injection.trigger.id": left.injection_trigger_id,
                "injection.trigger.sha256": left.injection_trigger_sha256,
            }
