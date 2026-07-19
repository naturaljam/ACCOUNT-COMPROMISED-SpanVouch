from __future__ import annotations

from spanvouch.labs.opslab.models import (
    OpsFailureFamily,
    OpsFaultProfile,
    OpsOperation,
    OpsScenarioTemplate,
)

_EVIDENCE = ("tool.name", "tool.result", "tool.error.message", "run.outcome")
_TERMINAL = "opslab-terminal-v1"


def _operation_plan(*operations: str) -> tuple[OpsOperation, ...]:
    return tuple(OpsOperation(operation=item) for item in operations)


def _family_templates(
    *,
    family: OpsFailureFamily,
    request: str,
    operation_plan: tuple[OpsOperation, ...],
    faults: tuple[tuple[str, str, int], ...],
    healthy_template_id: str,
) -> tuple[OpsScenarioTemplate, ...]:
    templates = tuple(
        OpsScenarioTemplate(
            template_id=template_id,
            family=family,
            user_request=request,
            operation_plan=operation_plan,
            injection=OpsFaultProfile(
                fault_code=template_id,
                operation=operation,
                attempt=attempt,
            ),
            terminal_predicate_id=_TERMINAL,
            required_evidence_selectors=_EVIDENCE,
        )
        for template_id, operation, attempt in faults
    )
    return templates + (
        OpsScenarioTemplate(
            template_id=healthy_template_id,
            family=family,
            user_request=request,
            operation_plan=operation_plan,
            injection=None,
            terminal_predicate_id=_TERMINAL,
            required_evidence_selectors=_EVIDENCE,
        ),
    )


def build_opslab_templates() -> tuple[OpsScenarioTemplate, ...]:
    timeout = _family_templates(
        family=OpsFailureFamily.TIMEOUT,
        request="Complete one upstream operation within its logical deadline.",
        operation_plan=_operation_plan(
            "prepare-request", "call-upstream", "record-response"
        ),
        faults=(
            ("timeout-no-retry", "call-upstream", 1),
            ("timeout-unbounded-retry", "call-upstream", 1),
            ("retry-amplification", "call-upstream", 1),
        ),
        healthy_template_id="timeout-control",
    )
    resource = _family_templates(
        family=OpsFailureFamily.RESOURCE,
        request="Complete work within deterministic capacity and degrade safely.",
        operation_plan=_operation_plan(
            "inspect-capacity", "reserve-token", "perform-work", "apply-degradation"
        ),
        faults=(
            ("rate-limit-unhandled", "reserve-token", 1),
            ("resource-exhaustion", "perform-work", 1),
            ("degradation-missing", "apply-degradation", 1),
        ),
        healthy_template_id="resource-control",
    )
    concurrency = _family_templates(
        family=OpsFailureFamily.CONCURRENCY,
        request="Acquire ordered leases and commit one deterministic operation.",
        operation_plan=_operation_plan(
            "acquire-alpha", "acquire-beta", "renew-lease", "commit-work"
        ),
        faults=(
            ("lease-expiry", "renew-lease", 1),
            ("lock-contention", "acquire-alpha", 1),
            ("deadlock-cycle", "acquire-beta", 1),
        ),
        healthy_template_id="concurrency-control",
    )
    recovery = _family_templates(
        family=OpsFailureFamily.RECOVERY,
        request="Resume from a versioned checkpoint without duplicate effects.",
        operation_plan=_operation_plan(
            "load-checkpoint", "apply-operation", "save-checkpoint", "resume-workflow"
        ),
        faults=(
            ("checkpoint-stale", "load-checkpoint", 1),
            ("resume-duplicate", "apply-operation", 1),
            ("workflow-state-drift", "resume-workflow", 1),
        ),
        healthy_template_id="recovery-control",
    )
    return timeout + resource + concurrency + recovery
