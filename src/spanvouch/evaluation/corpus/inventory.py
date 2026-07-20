"""Authoritative execution-only Phase 5 scenario inventory."""

from __future__ import annotations

from spanvouch.labs.opslab.templates import build_opslab_templates
from spanvouch.labs.runtime import LabScenario
from spanvouch.labs.supportlab.runtime import build_support_lab_scenarios


def build_phase5_execution_inventory(seed: int) -> tuple[LabScenario, ...]:
    """Return the frozen ordered 20 SupportLab + 16 OpsLab execution catalog."""
    supportlab = build_support_lab_scenarios(seed)
    opslab = tuple(template.to_lab_scenario() for template in build_opslab_templates())
    inventory = (*supportlab, *opslab)
    identities = tuple(
        (scenario.domain, scenario.template_id, scenario.scenario_id)
        for scenario in inventory
    )
    if len(supportlab) != 20 or len(opslab) != 16 or len(set(identities)) != 36:
        raise ValueError("authoritative Phase 5 execution inventory is invalid")
    return inventory
