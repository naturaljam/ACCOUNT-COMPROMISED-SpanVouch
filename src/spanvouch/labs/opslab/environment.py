from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from spanvouch.contracts.sanitization import sanitize_diagnostic_value
from spanvouch.contracts.versioning import canonical_json
from spanvouch.labs.opslab.models import (
    DeterministicTokenBucket,
    LockLeaseResult,
    LogicalClock,
    OpsFailureFamily,
    OpsFaultProfile,
    OpsOperation,
    OrderedLockLeaseTable,
    VersionedCheckpointStore,
)
from spanvouch.labs.opslab.templates import build_opslab_templates
from spanvouch.labs.runtime import (
    AgentAction,
    ExecutionStatus,
    LabScenario,
    RuntimeFailure,
    RuntimeFailureCategory,
    RuntimeState,
    ToolObservation,
)

_MAX_STEPS = 8


class OpsLabIncompatibilityError(RuntimeError):
    def __init__(self, failure: RuntimeFailure) -> None:
        super().__init__(failure.code)
        self.failure = failure


class OpsLabEnvironment:
    def __init__(self, *, scenario: LabScenario, max_steps: int = _MAX_STEPS) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.scenario = scenario
        self._family = OpsFailureFamily(scenario.failure_family)
        self._operations = _operations(scenario)
        self._injection = _injection(scenario)
        self._max_steps = max_steps
        self._index = 0
        self._attempts: dict[str, int] = {}
        self._terminal: ExecutionStatus | None = None
        self._clock = LogicalClock()
        self._bucket = DeterministicTokenBucket(capacity=2, tokens=2)
        self._locks = OrderedLockLeaseTable()
        self._checkpoints = VersionedCheckpointStore()
        initial = self._checkpoints.save("workflow", state_hash="state-initial")
        self._checkpoint_version = initial.version
        self._idempotency_key = "operation-001"
        self._applied_keys: set[str] = set()
        self._replay_count = 0
        self._state_hashes = [initial.state_hash]
        self._upstream_calls = 0
        self._backoff = 0
        self._rejection = False
        self._degradation_result = "not-needed"
        self._owner = "none"
        self._lease_version = 0
        self._lease_expiry = 0
        self._wait_for_edges: tuple[tuple[str, str], ...] = ()
        self._acquisition_result = "not-attempted"

    async def decide(self, state: RuntimeState) -> AgentAction:
        del state
        if self._terminal is not None or self._index >= len(self._operations):
            return AgentAction(kind="final", final_message="OpsLab operation completed.")
        operation = self._operations[self._index]
        attempt = self._attempts.get(operation.operation, 0) + 1
        return AgentAction(
            kind="tool",
            tool_name=operation.operation,
            arguments={"reason": f"attempt-{attempt}"},
        )

    async def execute(self, action: AgentAction) -> ToolObservation:
        if action.kind != "tool" or action.tool_name is None:
            raise ValueError("OpsLab can execute only tool actions")
        if self._index >= len(self._operations):
            raise ValueError("operation plan is already complete")
        operation = self._operations[self._index]
        if action.tool_name != operation.operation:
            raise ValueError("tool action does not match the ordered operation plan")
        attempt = self._attempts.get(operation.operation, 0) + 1
        self._attempts[operation.operation] = attempt
        self._clock.advance()
        success, retryable = self._execute_operation(operation.operation, attempt)
        evidence = canonical_json(self._evidence())
        if success:
            self._index += 1
            if self._index == len(self._operations):
                self._terminal = ExecutionStatus.SUCCEEDED
            return ToolObservation(
                tool_name=operation.operation,
                result=_sanitize_text(evidence),
                status="ok",
                retryable=False,
            )
        if not retryable:
            self._terminal = ExecutionStatus.FAILED
        return ToolObservation(
            tool_name=operation.operation,
            error={
                "type": "OpsFault",
                "message": _sanitize_text(evidence),
                "exception_type": "spanvouch.labs.opslab.OpsFault",
            },
            status="error",
            retryable=retryable,
        )

    def injection_trigger(
        self,
        state: RuntimeState,
        action: AgentAction,
    ) -> tuple[str, str] | None:
        del state
        if self._injection is None or action.tool_name is None:
            return None
        attempt = self._attempts.get(action.tool_name, 0) + 1
        if (
            action.tool_name != self._injection.operation
            or attempt != self._injection.attempt
        ):
            return None
        trigger_id = f"{action.tool_name}.{attempt}"
        return trigger_id, self.scenario.injection_trigger_digest(trigger_id)

    def terminal_status(self, state: RuntimeState) -> ExecutionStatus | None:
        if state.final_message is not None:
            return ExecutionStatus.SUCCEEDED
        if state.failure is not None:
            if state.failure.category is RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY:
                return ExecutionStatus.INCOMPATIBLE
            return ExecutionStatus.FAILED
        if self._terminal is not None:
            return self._terminal
        if state.step >= self._max_steps:
            return ExecutionStatus.STEP_LIMIT
        return None

    def _execute_operation(self, operation: str, attempt: int) -> tuple[bool, bool]:
        if self._family is OpsFailureFamily.TIMEOUT:
            return self._execute_timeout(operation, attempt)
        if self._family is OpsFailureFamily.RESOURCE:
            return self._execute_resource(operation, attempt)
        if self._family is OpsFailureFamily.CONCURRENCY:
            return self._execute_concurrency(operation, attempt)
        return self._execute_recovery(operation, attempt)

    def _execute_timeout(self, operation: str, attempt: int) -> tuple[bool, bool]:
        if operation != "call-upstream":
            return True, False
        self._upstream_calls += 1
        if not self._matches_injection(operation, 1):
            return True, False
        code = cast(OpsFaultProfile, self._injection).fault_code
        if code == "timeout-no-retry":
            return False, False
        if code == "timeout-unbounded-retry":
            self._backoff += 1
            return False, True
        self._backoff += attempt
        return (False, attempt < 3)

    def _execute_resource(self, operation: str, attempt: int) -> tuple[bool, bool]:
        if self._matches_injection(operation, attempt):
            code = cast(OpsFaultProfile, self._injection).fault_code
            if code == "rate-limit-unhandled":
                self._rejection = True
                return False, False
            if code == "resource-exhaustion":
                self._rejection = not self._bucket.consume(2)
                return False, False
            if code == "degradation-missing":
                self._degradation_result = "missing"
                return False, False
        if operation in {"reserve-token", "perform-work"} and not self._bucket.consume():
            self._rejection = True
            return False, False
        if operation == "apply-degradation":
            self._degradation_result = "healthy-path"
        return True, False

    def _execute_concurrency(self, operation: str, attempt: int) -> tuple[bool, bool]:
        if operation == "acquire-alpha":
            if self._matches_injection(operation, attempt):
                self._locks.acquire(
                    "alpha", owner="worker-b", now=self._clock.now, lease_ticks=4
                )
            result = self._locks.acquire(
                "alpha", owner="worker-a", now=self._clock.now, lease_ticks=3
            )
            self._record_lease(result)
            return result.acquired, False
        if operation == "acquire-beta":
            if self._matches_injection(operation, attempt):
                self._locks.acquire(
                    "beta", owner="worker-b", now=self._clock.now, lease_ticks=4
                )
            result = self._locks.acquire(
                "beta", owner="worker-a", now=self._clock.now, lease_ticks=3
            )
            self._record_lease(result)
            if not result.acquired and self._matches_injection(operation, attempt):
                self._wait_for_edges = (*result.wait_for_edges, ("worker-b", "worker-a"))
            return result.acquired, False
        if operation == "renew-lease":
            if self._matches_injection(operation, attempt):
                self._clock.advance(4)
                self._locks.acquire(
                    "alpha", owner="worker-b", now=self._clock.now, lease_ticks=3
                )
            result = self._locks.acquire(
                "alpha", owner="worker-a", now=self._clock.now, lease_ticks=3
            )
            self._record_lease(result)
            return result.acquired, False
        self._locks.release("beta", owner="worker-a")
        self._locks.release("alpha", owner="worker-a")
        self._acquisition_result = "committed"
        return True, False

    def _record_lease(self, result: LockLeaseResult) -> None:
        self._owner = result.owner
        self._lease_version = result.version
        self._lease_expiry = result.expiry
        self._wait_for_edges = result.wait_for_edges
        self._acquisition_result = "acquired" if result.acquired else "blocked"

    def _execute_recovery(self, operation: str, attempt: int) -> tuple[bool, bool]:
        if operation == "load-checkpoint":
            if self._matches_injection(operation, attempt):
                self._replay_count += 1
                return False, False
            checkpoint = self._checkpoints.load("workflow")
            self._checkpoint_version = checkpoint.version
            return True, False
        if operation == "apply-operation":
            if self._matches_injection(operation, attempt):
                self._applied_keys.add(self._idempotency_key)
                self._replay_count = 2
                self._state_hashes.append("state-duplicate")
                return False, False
            self._applied_keys.add(self._idempotency_key)
            self._state_hashes.append("state-applied")
            return True, False
        if operation == "save-checkpoint":
            checkpoint = self._checkpoints.save(
                "workflow", state_hash=self._state_hashes[-1]
            )
            self._checkpoint_version = checkpoint.version
            return True, False
        if self._matches_injection(operation, attempt):
            self._state_hashes.append("state-drifted")
            return False, False
        self._replay_count = 1
        return True, False

    def _matches_injection(self, operation: str, attempt: int) -> bool:
        return (
            self._injection is not None
            and self._injection.operation == operation
            and self._injection.attempt == attempt
        )

    def _evidence(self) -> dict[str, JsonValue]:
        if self._family is OpsFailureFamily.TIMEOUT:
            policy = "bounded-2"
            if self._injection is not None:
                policy = {
                    "timeout-no-retry": "none",
                    "timeout-unbounded-retry": "unbounded",
                    "retry-amplification": "bounded-3",
                }[self._injection.fault_code]
            return {
                "deadline": 10,
                "attempts": self._attempts.get("call-upstream", 0),
                "retry_policy": policy,
                "backoff": self._backoff,
                "upstream_calls": self._upstream_calls,
            }
        if self._family is OpsFailureFamily.RESOURCE:
            return {
                "capacity": self._bucket.capacity,
                "remaining_tokens": self._bucket.remaining_tokens,
                "rejection": self._rejection,
                "degradation_result": self._degradation_result,
            }
        if self._family is OpsFailureFamily.CONCURRENCY:
            return {
                "owner": self._owner,
                "lease_version": self._lease_version,
                "lease_expiry": self._lease_expiry,
                "wait_for_edges": [list(edge) for edge in self._wait_for_edges],
                "acquisition_result": self._acquisition_result,
            }
        return {
            "checkpoint_version": self._checkpoint_version,
            "idempotency_key": self._idempotency_key,
            "replay_count": self._replay_count,
            "state_hashes": list(self._state_hashes),
        }


class OpsLabEnvironmentRegistry:
    def __init__(self, *, max_steps: int = _MAX_STEPS) -> None:
        self._max_steps = max_steps
        self._scenario_ids = frozenset(
            item.template_id for item in build_opslab_templates()
        )

    def build(self, scenario: LabScenario) -> OpsLabEnvironment:
        if scenario.domain != "opslab":
            raise _incompatibility("unsupported_domain", scenario.domain)
        if scenario.scenario_id not in self._scenario_ids:
            raise _incompatibility("unsupported_scenario", scenario.scenario_id)
        return OpsLabEnvironment(scenario=scenario, max_steps=self._max_steps)


def _operations(scenario: LabScenario) -> tuple[OpsOperation, ...]:
    raw = scenario.parameters.get("operation_plan")
    if not isinstance(raw, list):
        raise ValueError("OpsLab operation_plan must be a list")
    return tuple(OpsOperation.model_validate(item) for item in raw)


def _injection(scenario: LabScenario) -> OpsFaultProfile | None:
    if not scenario.injection:
        return None
    return OpsFaultProfile.model_validate(scenario.injection)


def _incompatibility(code: str, value: str) -> OpsLabIncompatibilityError:
    return OpsLabIncompatibilityError(
        RuntimeFailure.from_message(
            category=RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY,
            code=code,
            retryable=False,
            sanitized_message=_sanitize_text(value),
        )
    )


def _sanitize_text(value: str) -> str:
    return cast(str, sanitize_diagnostic_value(value))
