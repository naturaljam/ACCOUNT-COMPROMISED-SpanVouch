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
