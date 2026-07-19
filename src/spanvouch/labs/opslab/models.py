from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.labs.runtime import LabScenario


class OpsFailureFamily(StrEnum):
    TIMEOUT = "timeout"
    RESOURCE = "resource"
    CONCURRENCY = "concurrency"
    RECOVERY = "recovery"


class OpsFaultProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fault_code: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    attempt: int = Field(ge=1)


class OpsOperation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class OpsScenarioTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str = Field(min_length=1)
    family: OpsFailureFamily
    user_request: str = Field(min_length=1)
    operation_plan: tuple[OpsOperation, ...] = Field(min_length=1)
    injection: OpsFaultProfile | None
    terminal_predicate_id: str = Field(min_length=1)
    required_evidence_selectors: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_injection_operation(self) -> OpsScenarioTemplate:
        if self.injection is not None and self.injection.operation not in {
            item.operation for item in self.operation_plan
        }:
            raise ValueError("injection operation must appear in the operation plan")
        return self

    def to_lab_scenario(self) -> LabScenario:
        injection: dict[str, JsonValue] = {}
        if self.injection is not None:
            injection = self.injection.model_dump(mode="json")
        return LabScenario(
            scenario_id=self.template_id,
            template_id=self.template_id,
            domain="opslab",
            failure_family=self.family.value,
            user_request=self.user_request,
            parameters={
                "operation_plan": [
                    item.model_dump(mode="json") for item in self.operation_plan
                ]
            },
            injection=injection,
            tool_contract_sha256=canonical_sha256(OPS_TOOL_CONTRACT),
            terminal_predicate_id=self.terminal_predicate_id,
            allowed_evidence_selectors=self.required_evidence_selectors,
        )


OPS_TOOL_CONTRACT: JsonValue = {
    "operation": {"arguments": ["reason"], "result": "canonical evidence snapshot"}
}


@dataclass
class LogicalClock:
    now: int = 0

    def advance(self, ticks: int = 1) -> int:
        if ticks < 0:
            raise ValueError("clock ticks must be non-negative")
        self.now += ticks
        return self.now


@dataclass
class DeterministicTokenBucket:
    capacity: int
    tokens: int

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError("capacity must be positive")
        if not 0 <= self.tokens <= self.capacity:
            raise ValueError("tokens must fit within capacity")

    @property
    def remaining_tokens(self) -> int:
        return self.tokens

    def consume(self, amount: int = 1) -> bool:
        if amount < 1:
            raise ValueError("token amount must be positive")
        if amount > self.tokens:
            return False
        self.tokens -= amount
        return True


@dataclass(frozen=True)
class LockLeaseResult:
    acquired: bool
    owner: str
    version: int
    expiry: int
    wait_for_edges: tuple[tuple[str, str], ...]


@dataclass
class _Lease:
    owner: str
    version: int
    expiry: int


class OrderedLockLeaseTable:
    def __init__(self) -> None:
        self._leases: dict[str, _Lease] = {}
        self._wait_for_edges: list[tuple[str, str]] = []

    @property
    def wait_for_edges(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._wait_for_edges)

    def acquire(
        self,
        resource: str,
        *,
        owner: str,
        now: int,
        lease_ticks: int,
    ) -> LockLeaseResult:
        if lease_ticks < 1:
            raise ValueError("lease_ticks must be positive")
        current = self._leases.get(resource)
        if current is not None and current.expiry > now and current.owner != owner:
            edge = (owner, current.owner)
            if edge not in self._wait_for_edges:
                self._wait_for_edges.append(edge)
            return LockLeaseResult(
                acquired=False,
                owner=current.owner,
                version=current.version,
                expiry=current.expiry,
                wait_for_edges=(edge,),
            )
        version = 1 if current is None else current.version + 1
        lease = _Lease(owner=owner, version=version, expiry=now + lease_ticks)
        self._leases[resource] = lease
        return LockLeaseResult(
            acquired=True,
            owner=owner,
            version=version,
            expiry=lease.expiry,
            wait_for_edges=(),
        )

    def release(self, resource: str, *, owner: str) -> bool:
        current = self._leases.get(resource)
        if current is None or current.owner != owner:
            return False
        del self._leases[resource]
        return True


@dataclass(frozen=True)
class Checkpoint:
    workflow_id: str
    version: int
    state_hash: str


class VersionedCheckpointStore:
    def __init__(self) -> None:
        self._values: dict[str, list[Checkpoint]] = {}

    def save(self, workflow_id: str, *, state_hash: str) -> Checkpoint:
        versions = self._values.setdefault(workflow_id, [])
        checkpoint = Checkpoint(
            workflow_id=workflow_id,
            version=len(versions) + 1,
            state_hash=state_hash,
        )
        versions.append(checkpoint)
        return checkpoint

    def load(self, workflow_id: str, *, version: int | None = None) -> Checkpoint:
        versions = self._values.get(workflow_id, [])
        if not versions:
            raise KeyError(workflow_id)
        if version is None:
            return versions[-1]
        if version < 1 or version > len(versions):
            raise KeyError(f"{workflow_id}:{version}")
        return versions[version - 1]
