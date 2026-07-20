"""Sealed Phase 5 scenario-to-gold mapping.

This module is evaluator-only. Stage A must never import it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

FormalSplit = Literal["train", "validation", "test"]


@dataclass(frozen=True)
class GoldSpec:
    expected_failure_type: str
    causal_chain_expectations: tuple[str, ...]
    evidence_expectations: tuple[str, ...]
    control: bool
    split: FormalSplit


def _support_specs() -> dict[str, GoldSpec]:
    controls = {
        f"clean-{index:02d}": GoldSpec(
            expected_failure_type="no_failure",
            causal_chain_expectations=(),
            evidence_expectations=("run.outcome",),
            control=True,
            split=("test" if index == 4 else "validation" if index == 2 else "train"),
        )
        for index in range(1, 5)
    }
    families = {
        "wrong_tool": (("tool_selection", "unexpected_tool"), ("tool.name",)),
        "invalid_argument": (
            ("argument_construction", "invalid_argument"),
            ("tool.arguments.amount", "tool.error.type"),
        ),
        "missing_precondition": (
            ("precondition_check", "required_lookup_omitted"),
            ("tool.name", "run.outcome"),
        ),
        "ignored_tool_error": (
            ("tool_error", "error_ignored", "unsafe_continuation"),
            ("tool.error.type", "run.outcome"),
        ),
        "context_corruption": (
            ("tool_result", "context_corruption", "invalid_action"),
            ("tool.result", "tool.arguments.order_id"),
        ),
        "policy_violation": (
            ("policy_check", "approval_bypassed", "unsafe_action"),
            ("tool.arguments.approval", "tool.name"),
        ),
        "loop_or_budget_exhaustion": (
            ("repeated_action", "budget_exhausted"),
            ("tool.name", "run.outcome"),
        ),
        "invalid_final_state": (
            ("premature_finalization", "invalid_terminal_state"),
            ("run.final_message", "run.outcome"),
        ),
    }
    failures = {
        f"{family}-{index:02d}": GoldSpec(
            expected_failure_type=family,
            causal_chain_expectations=causal,
            evidence_expectations=evidence,
            control=False,
            split="train" if index == 1 else "test",
        )
        for family, (causal, evidence) in families.items()
        for index in range(1, 3)
    }
    return controls | failures


def _ops_specs() -> dict[str, GoldSpec]:
    families = {
        "timeout": (
            "timeout-no-retry",
            "timeout-unbounded-retry",
            "retry-amplification",
            "timeout-control",
        ),
        "resource": (
            "rate-limit-unhandled",
            "resource-exhaustion",
            "degradation-missing",
            "resource-control",
        ),
        "concurrency": (
            "lease-expiry",
            "lock-contention",
            "deadlock-cycle",
            "concurrency-control",
        ),
        "recovery": (
            "checkpoint-stale",
            "resume-duplicate",
            "workflow-state-drift",
            "recovery-control",
        ),
    }
    specs: dict[str, GoldSpec] = {}
    for family, template_ids in families.items():
        for index, template_id in enumerate(template_ids):
            control = template_id.endswith("-control")
            specs[template_id] = GoldSpec(
                expected_failure_type="no_failure" if control else template_id,
                causal_chain_expectations=() if control else (family, template_id),
                evidence_expectations=("run.outcome",) if control else (
                    "tool.name",
                    "tool.result",
                    "tool.error.message",
                    "run.outcome",
                ),
                control=control,
                split=(
                    "test"
                    if control or index == 2
                    else "validation"
                    if index == 1
                    else "train"
                ),
            )
    return specs


GOLD_SPECS: Mapping[str, GoldSpec] = MappingProxyType(_support_specs() | _ops_specs())
