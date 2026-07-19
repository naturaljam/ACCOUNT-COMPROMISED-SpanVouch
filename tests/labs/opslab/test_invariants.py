from __future__ import annotations

import pytest

from spanvouch.labs.frameworks.langgraph import LangGraphRuntimeAdapter
from spanvouch.labs.opslab import OpsFailureFamily, build_opslab_templates
from spanvouch.labs.opslab.environment import OpsLabEnvironmentRegistry
from spanvouch.labs.opslab.invariants import opslab_rules
from spanvouch.labs.runtime import ExecutionProvenance, RuntimeConfig
from spanvouch.trace.diagnostic_view import TraceProjector
from spanvouch.trace.evidence_catalog import EvidenceCatalog
from spanvouch.verification.invariants import InvariantStatus, RuleContext


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


async def _context(template_id: str) -> RuleContext:
    template = next(
        item for item in build_opslab_templates() if item.template_id == template_id
    )
    record = await LangGraphRuntimeAdapter(
        OpsLabEnvironmentRegistry(), provenance=_provenance()
    ).execute(template.to_lab_scenario(), _config())
    diagnostic = TraceProjector().project(record.trace)
    return RuleContext(
        view=diagnostic.view,
        evidence=EvidenceCatalog.from_context(diagnostic),
    )


@pytest.mark.asyncio
async def test_family_rules_distinguish_three_faults_from_healthy_path() -> None:
    rules = {rule.rule_id: rule for rule in opslab_rules()}

    for family in OpsFailureFamily:
        rule = rules[f"opslab.{family.value}"]
        family_templates = [
            item for item in build_opslab_templates() if item.family is family
        ]
        for template in family_templates:
            result = rule.evaluate(await _context(template.template_id))
            assert result.rule_id == f"opslab.{family.value}"
            assert result.rule_version == "1.0"
            if template.injection is None:
                assert result.status is InvariantStatus.PASSED
                assert result.hard_failure is False
            else:
                assert result.status is InvariantStatus.FAILED
                assert result.hard_failure is True
                assert result.evidence


@pytest.mark.asyncio
async def test_final_state_rule_passes_controls_and_flags_fault_outcomes() -> None:
    rule = next(rule for rule in opslab_rules() if rule.rule_id == "opslab.final_state")

    for template in build_opslab_templates():
        result = rule.evaluate(await _context(template.template_id))
        if template.injection is None:
            assert result.status is InvariantStatus.PASSED
        else:
            assert result.status is InvariantStatus.FAILED


def test_rules_do_not_import_framework_adapters() -> None:
    from pathlib import Path

    source = Path("src/spanvouch/labs/opslab/invariants.py").read_text(
        encoding="utf-8"
    )
    assert "spanvouch.labs.frameworks" not in source
    assert "langgraph" not in source.lower()
    assert "autogen" not in source.lower()
