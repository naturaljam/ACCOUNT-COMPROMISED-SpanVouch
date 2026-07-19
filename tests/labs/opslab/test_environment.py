from __future__ import annotations

import json

import pytest

from spanvouch.labs.opslab import build_opslab_templates
from spanvouch.labs.opslab.environment import OpsLabEnvironmentRegistry
from spanvouch.labs.opslab.models import (
    DeterministicTokenBucket,
    LogicalClock,
    OrderedLockLeaseTable,
    VersionedCheckpointStore,
)
from spanvouch.labs.runtime import ExecutionStatus, RuntimeState


async def _drive(template_id: str) -> tuple[ExecutionStatus, RuntimeState]:
    template = next(
        item for item in build_opslab_templates() if item.template_id == template_id
    )
    environment = OpsLabEnvironmentRegistry().build(template.to_lab_scenario())
    state = RuntimeState.initial()
    for _ in range(16):
        terminal = environment.terminal_status(state)
        if terminal is not None:
            return terminal, state
        action = await environment.decide(state)
        state = state.with_observation(await environment.execute(action))
    return ExecutionStatus.STEP_LIMIT, state


def _evidence(state: RuntimeState) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for observation in state.observations:
        raw = observation.result
        if raw is None:
            assert isinstance(observation.error, dict)
            raw = observation.error["message"]
        assert isinstance(raw, str)
        values.append(json.loads(raw))
    return values


@pytest.mark.asyncio
async def test_all_templates_terminate_with_observable_family_evidence() -> None:
    required = {
        "timeout": {
            "deadline",
            "attempts",
            "retry_policy",
            "backoff",
            "upstream_calls",
        },
        "resource": {
            "capacity",
            "remaining_tokens",
            "rejection",
            "degradation_result",
        },
        "concurrency": {
            "owner",
            "lease_version",
            "lease_expiry",
            "wait_for_edges",
            "acquisition_result",
        },
        "recovery": {
            "checkpoint_version",
            "idempotency_key",
            "replay_count",
            "state_hashes",
        },
    }

    for template in build_opslab_templates():
        terminal, state = await _drive(template.template_id)
        assert state.observations
        assert required[template.family.value] <= _evidence(state)[-1].keys()
        if template.injection is None:
            assert terminal is ExecutionStatus.SUCCEEDED
            assert all(item.status == "ok" for item in state.observations)
        else:
            assert terminal in {ExecutionStatus.FAILED, ExecutionStatus.STEP_LIMIT}


@pytest.mark.asyncio
async def test_environment_replay_is_deterministic() -> None:
    for template in build_opslab_templates():
        left = await _drive(template.template_id)
        right = await _drive(template.template_id)
        assert left == right


def test_logical_clock_and_token_bucket_are_counter_driven() -> None:
    clock = LogicalClock()
    bucket = DeterministicTokenBucket(capacity=2, tokens=2)

    assert clock.now == 0
    assert clock.advance(3) == 3
    assert bucket.consume(1) is True
    assert bucket.remaining_tokens == 1
    assert bucket.consume(2) is False
    assert bucket.remaining_tokens == 1


def test_ordered_locks_and_versioned_checkpoints_are_deterministic() -> None:
    locks = OrderedLockLeaseTable()
    checkpoints = VersionedCheckpointStore()

    first = locks.acquire("alpha", owner="worker-a", now=0, lease_ticks=2)
    blocked = locks.acquire("alpha", owner="worker-b", now=1, lease_ticks=2)
    renewed = locks.acquire("alpha", owner="worker-b", now=2, lease_ticks=2)
    v1 = checkpoints.save("workflow", state_hash="hash-a")
    v2 = checkpoints.save("workflow", state_hash="hash-b")

    assert (first.acquired, first.version, first.expiry) == (True, 1, 2)
    assert blocked.acquired is False
    assert blocked.wait_for_edges == (("worker-b", "worker-a"),)
    assert (renewed.acquired, renewed.version, renewed.expiry) == (True, 2, 4)
    assert (v1.version, v2.version) == (1, 2)
    assert checkpoints.load("workflow", version=1) == v1
    assert checkpoints.load("workflow") == v2


def test_lock_table_derives_a_cycle_from_two_blocked_acquisitions() -> None:
    locks = OrderedLockLeaseTable()
    locks.acquire("alpha", owner="worker-a", now=0, lease_ticks=4)
    locks.acquire("beta", owner="worker-b", now=0, lease_ticks=4)

    first_wait = locks.acquire("beta", owner="worker-a", now=1, lease_ticks=2)
    second_wait = locks.acquire("alpha", owner="worker-b", now=1, lease_ticks=2)

    assert first_wait.wait_for_edges == (("worker-a", "worker-b"),)
    assert second_wait.wait_for_edges == (("worker-b", "worker-a"),)
    assert locks.wait_for_edges == (
        ("worker-a", "worker-b"),
        ("worker-b", "worker-a"),
    )


@pytest.mark.asyncio
async def test_recovery_faults_expose_real_version_idempotency_and_hash_checks() -> None:
    stale_status, stale_state = await _drive("checkpoint-stale")
    duplicate_status, duplicate_state = await _drive("resume-duplicate")
    drift_status, drift_state = await _drive("workflow-state-drift")
    stale = _evidence(stale_state)[-1]
    duplicate = _evidence(duplicate_state)[-1]
    drift = _evidence(drift_state)[-1]

    assert stale_status is ExecutionStatus.FAILED
    assert stale["current_checkpoint_version"] == 2
    assert stale["loaded_checkpoint_version"] == 1
    assert stale["state_hash_match"] is False
    assert duplicate_status is ExecutionStatus.FAILED
    assert duplicate["replay_count"] == 2
    assert duplicate["effect_count"] == 1
    assert drift_status is ExecutionStatus.FAILED
    assert drift["checkpoint_state_hash"] == "state-applied"
    assert drift["current_state_hash"] == "state-drifted"
    assert drift["state_hash_match"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("template_id", "terminal", "tool_name", "values"),
    [
        (
            "timeout-no-retry",
            ExecutionStatus.FAILED,
            "call-upstream",
            {"retry_policy": "none", "attempts": 1, "upstream_calls": 1, "backoff": 0},
        ),
        (
            "timeout-unbounded-retry",
            ExecutionStatus.STEP_LIMIT,
            "call-upstream",
            {"retry_policy": "unbounded", "attempts": 7, "upstream_calls": 7, "backoff": 7},
        ),
        (
            "retry-amplification",
            ExecutionStatus.FAILED,
            "call-upstream",
            {"retry_policy": "bounded-3", "attempts": 3, "upstream_calls": 3, "backoff": 6},
        ),
        (
            "rate-limit-unhandled",
            ExecutionStatus.FAILED,
            "reserve-token",
            {"remaining_tokens": 2, "rejection": True, "degradation_result": "not-needed"},
        ),
        (
            "resource-exhaustion",
            ExecutionStatus.FAILED,
            "perform-work",
            {"remaining_tokens": 1, "rejection": True, "degradation_result": "not-needed"},
        ),
        (
            "degradation-missing",
            ExecutionStatus.FAILED,
            "apply-degradation",
            {"remaining_tokens": 0, "rejection": False, "degradation_result": "missing"},
        ),
        (
            "lease-expiry",
            ExecutionStatus.FAILED,
            "renew-lease",
            {"acquisition_result": "blocked", "wait_for_edges": [["worker-a", "worker-b"]]},
        ),
        (
            "lock-contention",
            ExecutionStatus.FAILED,
            "acquire-alpha",
            {"acquisition_result": "blocked", "wait_for_edges": [["worker-a", "worker-b"]]},
        ),
        (
            "deadlock-cycle",
            ExecutionStatus.FAILED,
            "acquire-beta",
            {
                "acquisition_result": "blocked",
                "wait_for_edges": [
                    ["worker-a", "worker-b"],
                    ["worker-b", "worker-a"],
                ],
            },
        ),
    ],
)
async def test_timeout_resource_and_concurrency_fault_oracles(
    template_id: str,
    terminal: ExecutionStatus,
    tool_name: str,
    values: dict[str, object],
) -> None:
    status, state = await _drive(template_id)
    evidence = _evidence(state)[-1]

    assert status is terminal
    assert state.observations[-1].tool_name == tool_name
    assert {key: evidence[key] for key in values} == values


def test_opslab_uses_no_nondeterministic_runtime_primitives() -> None:
    from pathlib import Path

    source = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in sorted(Path("src/spanvouch/labs/opslab").rglob("*.py"))
    )
    for forbidden in (
        "asyncio.sleep",
        "time.sleep",
        "threading",
        "multiprocessing",
        "random",
        "requests",
        "httpx",
    ):
        assert forbidden not in source
