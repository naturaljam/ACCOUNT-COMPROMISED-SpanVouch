from __future__ import annotations

from pathlib import Path

from spanvouch.labs.opslab import OpsFailureFamily, build_opslab_templates

EXPECTED = {
    "timeout": {
        "timeout-no-retry",
        "timeout-unbounded-retry",
        "retry-amplification",
        "timeout-control",
    },
    "resource": {
        "rate-limit-unhandled",
        "resource-exhaustion",
        "degradation-missing",
        "resource-control",
    },
    "concurrency": {
        "lease-expiry",
        "lock-contention",
        "deadlock-cycle",
        "concurrency-control",
    },
    "recovery": {
        "checkpoint-stale",
        "resume-duplicate",
        "workflow-state-drift",
        "recovery-control",
    },
}


def test_opslab_has_four_families_and_sixteen_templates() -> None:
    templates = build_opslab_templates()
    assert len(templates) == 16
    grouped = {
        family: {item.template_id for item in templates if item.family.value == family}
        for family in EXPECTED
    }
    assert grouped == EXPECTED
    assert sum(item.injection is None for item in templates) == 4


def test_family_templates_share_one_ordered_operation_shape() -> None:
    templates = build_opslab_templates()

    for family in OpsFailureFamily:
        plans = {
            tuple(operation.operation for operation in item.operation_plan)
            for item in templates
            if item.family is family
        }
        assert len(plans) == 1


def test_lab_scenario_contains_only_execution_trigger_metadata() -> None:
    for template in build_opslab_templates():
        scenario = template.to_lab_scenario()
        assert scenario.domain == "opslab"
        assert scenario.template_id == template.template_id
        assert scenario.scenario_id == template.template_id
        assert scenario.parameters == {
            "operation_plan": [
                {"operation": item.operation, "arguments": item.arguments}
                for item in template.operation_plan
            ]
        }
        if template.injection is None:
            assert scenario.injection == {}
        else:
            assert scenario.injection == {
                "fault_code": template.injection.fault_code,
                "operation": template.injection.operation,
                "attempt": template.injection.attempt,
            }


def test_opslab_source_has_no_label_or_control_fields() -> None:
    source_root = Path("src/spanvouch/labs/opslab")
    source = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in sorted(source_root.rglob("*.py"))
    )

    for forbidden in ("is_control", "expected", "gold"):
        assert forbidden not in source
