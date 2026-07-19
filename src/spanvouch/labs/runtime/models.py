from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Literal, NoReturn, Self, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from spanvouch.contracts.sanitization import ALLOWED_ATTRIBUTES, sanitize_diagnostic_value
from spanvouch.contracts.trace import TraceIR
from spanvouch.contracts.versioning import (
    IDENTIFIER_PATTERN,
    SHA256_PATTERN,
    canonical_sha256,
)

_GIT_COMMIT_PATTERN = r"^[0-9a-f]{40}$"
_FORBIDDEN_RECORD_FIELDS = frozenset(
    {
        "api_key",
        "authorization",
        "expected_critical_operation",
        "expected_failure",
        "expected_finding",
        "gold_label",
        "hidden_reasoning",
        "mutation_metadata",
        "prompt_text",
        "raw_response",
        "split_identity",
    }
)
_T = TypeVar("_T")


def _immutable_value_error() -> NoReturn:
    raise TypeError("runtime value is immutable")


class _FrozenDict(dict[str, _T]):
    def __init__(self, values: Mapping[str, _T]) -> None:
        dict.__init__(self, values)

    def __setitem__(self, key: str, value: _T) -> None:
        _immutable_value_error()

    def __delitem__(self, key: str) -> None:
        _immutable_value_error()

    def __ior__(self, other: Any) -> Any:  # type: ignore[misc]
        _immutable_value_error()

    def clear(self) -> None:
        _immutable_value_error()

    def pop(self, key: Any, default: Any = None) -> Any:
        _immutable_value_error()

    def popitem(self) -> NoReturn:
        _immutable_value_error()

    def setdefault(self, key: Any, default: Any = None) -> Any:
        _immutable_value_error()

    def update(self, other: Any = (), /, **kwargs: Any) -> None:
        _immutable_value_error()


class _FrozenList(list[_T]):
    def __init__(self, values: Iterable[_T]) -> None:
        list.__init__(self, values)

    def __setitem__(self, index: Any, value: Any) -> None:
        _immutable_value_error()

    def __delitem__(self, index: Any) -> None:
        _immutable_value_error()

    def __iadd__(self, values: Any) -> Any:  # type: ignore[misc]
        _immutable_value_error()

    def __imul__(self, count: Any) -> Any:  # type: ignore[misc]
        _immutable_value_error()

    def append(self, value: _T) -> None:
        _immutable_value_error()

    def clear(self) -> None:
        _immutable_value_error()

    def extend(self, values: Iterable[_T]) -> None:
        _immutable_value_error()

    def insert(self, index: Any, value: _T) -> None:
        _immutable_value_error()

    def pop(self, index: Any = -1) -> Any:
        _immutable_value_error()

    def remove(self, value: _T) -> None:
        _immutable_value_error()

    def reverse(self) -> None:
        _immutable_value_error()

    def sort(self, *, key: object = None, reverse: bool = False) -> None:
        _immutable_value_error()


def _freeze_json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return cast(
            JsonValue,
            _FrozenDict(
                {key: _freeze_json_value(item) for key, item in value.items()}
            ),
        )
    if isinstance(value, list):
        return cast(JsonValue, _FrozenList(_freeze_json_value(item) for item in value))
    return value


def _freeze_json_dict(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return _FrozenDict({key: _freeze_json_value(item) for key, item in value.items()})


class FrameworkId(StrEnum):
    LANGGRAPH = "langgraph"
    AUTOGEN = "autogen"


class ExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STEP_LIMIT = "step_limit"
    INCOMPATIBLE = "incompatible"


class RuntimeFailureCategory(StrEnum):
    FRAMEWORK_EXECUTION = "framework_execution_failure"
    FRAMEWORK_INCOMPATIBILITY = "framework_incompatibility"
    INFRASTRUCTURE = "infrastructure_failure"


class ParityDimension(StrEnum):
    SCENARIO_INPUT = "scenario_input"
    TOOL_SEQUENCE = "tool_sequence"
    TOOL_ARGUMENTS = "tool_arguments"
    TOOL_RESULTS = "tool_results"
    INJECTION_TRIGGER = "injection_trigger"
    RUNTIME_LIMIT = "runtime_limit"
    TERMINAL_PREDICATE = "terminal_predicate"
    OUTCOME = "outcome"
    EVIDENCE_SELECTOR = "evidence_selector"


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    seed: int
    repetition: int = Field(ge=1)
    max_steps: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    max_tool_calls: int = Field(ge=1)


class LabScenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    domain: Literal["supportlab", "opslab"]
    failure_family: str = Field(min_length=1)
    user_request: str = Field(min_length=1)
    parameters: dict[str, JsonValue]
    injection: dict[str, JsonValue]
    tool_contract_sha256: str = Field(pattern=SHA256_PATTERN)
    terminal_predicate_id: str = Field(min_length=1)
    allowed_evidence_selectors: tuple[str, ...]

    @model_validator(mode="after")
    def freeze_json_inputs(self) -> Self:
        _reject_forbidden_field_names(self.parameters)
        _reject_forbidden_field_names(self.injection)
        object.__setattr__(self, "parameters", _freeze_json_dict(self.parameters))
        object.__setattr__(self, "injection", _freeze_json_dict(self.injection))
        return self


class AgentAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["tool", "final"]
    tool_name: str | None = Field(default=None, min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    final_message: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.kind == "tool":
            if self.tool_name is None:
                raise ValueError("tool action requires tool_name")
            if self.final_message is not None:
                raise ValueError("tool action forbids final_message")
            object.__setattr__(self, "arguments", _freeze_json_dict(self.arguments))
            return self
        if self.tool_name is not None or self.arguments:
            raise ValueError("final action forbids tool_name and arguments")
        if self.final_message is None:
            raise ValueError("final action requires final_message")
        object.__setattr__(self, "arguments", _freeze_json_dict(self.arguments))
        return self


class ToolObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: str = Field(min_length=1)
    result: JsonValue | None = None
    error: JsonValue | None = None
    status: Literal["ok", "error"]
    retryable: bool

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.status == "ok" and (self.result is None or self.error is not None):
            raise ValueError("ok observation requires only a result")
        if self.status == "error" and (self.error is None or self.result is not None):
            raise ValueError("error observation requires only an error")
        if self.result is not None:
            object.__setattr__(self, "result", _freeze_json_value(self.result))
        if self.error is not None:
            object.__setattr__(self, "error", _freeze_json_value(self.error))
        return self


class RuntimeFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    category: RuntimeFailureCategory
    code: str = Field(min_length=1)
    retryable: bool
    error_sha256: str = Field(pattern=SHA256_PATTERN)

    @classmethod
    def from_message(
        cls,
        *,
        category: RuntimeFailureCategory,
        code: str,
        retryable: bool,
        sanitized_message: str,
    ) -> Self:
        return cls(
            category=category,
            code=code,
            retryable=retryable,
            error_sha256=sha256(sanitized_message.encode("utf-8")).hexdigest(),
        )


class ParityMismatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: ParityDimension
    reference_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)


class ParityResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["matched", "mismatched", "incompatible"]
    mismatches: tuple[ParityMismatch, ...] = ()
    framework_incompatibility: RuntimeFailure | None = None
    incompatibility_code: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN,
    )

    @property
    def is_match(self) -> bool:
        return self.status == "matched"

    @model_validator(mode="after")
    def validate_result_shape(self) -> Self:
        if self.status == "matched":
            if self.mismatches:
                raise ValueError("matched parity result forbids mismatches")
            self._forbid_incompatibility_fields()
            return self
        if self.status == "mismatched":
            if not self.mismatches:
                raise ValueError("mismatched parity result requires mismatches")
            self._forbid_incompatibility_fields()
            return self
        if self.mismatches:
            raise ValueError("incompatible parity result forbids mismatches")
        if self.framework_incompatibility is None or self.incompatibility_code is None:
            raise ValueError(
                "incompatible parity result requires a typed failure and stable code"
            )
        if (
            self.framework_incompatibility.category
            is not RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY
        ):
            raise ValueError("incompatible parity result requires framework_incompatibility")
        if self.framework_incompatibility.code != self.incompatibility_code:
            raise ValueError("incompatibility code must match the typed failure")
        return self

    def _forbid_incompatibility_fields(self) -> None:
        if (
            self.framework_incompatibility is not None
            or self.incompatibility_code is not None
        ):
            raise ValueError(
                "only incompatible parity results may carry incompatibility fields"
            )


class RuntimeState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    step: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    observations: tuple[ToolObservation, ...]
    final_message: str | None = Field(default=None, min_length=1)
    failure: RuntimeFailure | None

    @model_validator(mode="after")
    def validate_terminal_state(self) -> Self:
        if self.final_message is not None and self.failure is not None:
            raise ValueError("runtime state has multiple terminal values")
        if self.tool_calls != len(self.observations):
            raise ValueError("tool_calls must equal observation count")
        if self.step < self.tool_calls:
            raise ValueError("step cannot be less than tool_calls")
        return self

    @classmethod
    def initial(cls) -> Self:
        return cls(
            step=0,
            tool_calls=0,
            observations=(),
            final_message=None,
            failure=None,
        )

    def with_observation(self, observation: ToolObservation) -> Self:
        self._require_active()
        return type(self).model_validate(
            {
                **self.model_dump(mode="python"),
                "step": self.step + 1,
                "tool_calls": self.tool_calls + 1,
                "observations": (*self.observations, observation),
            }
        )

    def with_final(self, final_message: str) -> Self:
        self._require_active()
        return type(self).model_validate(
            {**self.model_dump(mode="python"), "final_message": final_message}
        )

    def with_failure(self, failure: RuntimeFailure) -> Self:
        self._require_active()
        return type(self).model_validate(
            {**self.model_dump(mode="python"), "failure": failure}
        )

    def _require_active(self) -> None:
        if self.final_message is not None or self.failure is not None:
            raise ValueError("runtime state is already terminal")


class ExecutionProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    git_commit: str = Field(pattern=_GIT_COMMIT_PATTERN)
    package_version: str = Field(min_length=1)
    dependency_lock_sha256: str = Field(pattern=SHA256_PATTERN)
    dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    environment_sha256: str = Field(pattern=SHA256_PATTERN)
    tool_versions: dict[str, str]
    runtime_versions: dict[str, str]
    dirty_worktree: bool

    @model_validator(mode="after")
    def validate_version_order(self) -> Self:
        if tuple(self.tool_versions) != tuple(sorted(self.tool_versions)):
            raise ValueError("tool_versions must be sorted")
        if tuple(self.runtime_versions) != tuple(sorted(self.runtime_versions)):
            raise ValueError("runtime_versions must be sorted")
        object.__setattr__(self, "tool_versions", _FrozenDict(self.tool_versions))
        object.__setattr__(self, "runtime_versions", _FrozenDict(self.runtime_versions))
        return self


class ExecutionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trace: TraceIR
    trace_sha256: str = Field(pattern=SHA256_PATTERN)
    framework_id: FrameworkId
    framework_version: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    domain: Literal["supportlab", "opslab"]
    failure_family: str = Field(min_length=1)
    scenario_input_sha256: str = Field(pattern=SHA256_PATTERN)
    injection_trigger_sha256: str = Field(pattern=SHA256_PATTERN)
    terminal_predicate_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_selector_sha256: str = Field(pattern=SHA256_PATTERN)
    seed: int
    repetition: int = Field(ge=1)
    runtime_config: RuntimeConfig
    runtime_config_sha256: str = Field(pattern=SHA256_PATTERN)
    status: ExecutionStatus
    failure: RuntimeFailure | None
    started_at: datetime
    completed_at: datetime
    latency_seconds: float = Field(ge=0)
    steps: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    final_message: str | None = Field(default=None, min_length=1)
    provenance: ExecutionProvenance

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        _reject_forbidden_field_names(self.trace)
        projected_trace = _project_trace(self.trace)
        if projected_trace != self.trace:
            raise ValueError("trace attributes must be allowlisted and sanitized")
        object.__setattr__(self, "trace", _freeze_trace(self.trace))
        if canonical_sha256(self.trace) != self.trace_sha256:
            raise ValueError("trace_sha256 does not match trace")
        if canonical_sha256(self.runtime_config) != self.runtime_config_sha256:
            raise ValueError("runtime_config_sha256 does not match runtime_config")
        if (
            self.seed != self.runtime_config.seed
            or self.repetition != self.runtime_config.repetition
        ):
            raise ValueError("execution seed/repetition do not match runtime_config")
        if self.final_message is not None and (
            _sanitize_text(self.final_message) != self.final_message
        ):
            raise ValueError("final_message must be sanitized")
        self._validate_timings()
        self._validate_status()
        _reject_forbidden_field_names(self.model_dump(mode="python"))
        return self

    def _validate_timings(self) -> None:
        if not _is_utc(self.started_at) or not _is_utc(self.completed_at):
            raise ValueError("started_at and completed_at must be UTC timestamps")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        elapsed = (self.completed_at - self.started_at).total_seconds()
        if abs(elapsed - self.latency_seconds) > 1e-9:
            raise ValueError("latency_seconds does not match timestamps")

    def _validate_status(self) -> None:
        if self.status is ExecutionStatus.SUCCEEDED:
            if self.failure is not None:
                raise ValueError("succeeded execution forbids failure")
            if self.final_message is None:
                raise ValueError("succeeded execution requires final_message")
            return
        if self.failure is None:
            raise ValueError("non-succeeded execution requires failure")
        if self.final_message is not None:
            raise ValueError("non-succeeded execution forbids final_message")
        incompatible = self.failure.category is RuntimeFailureCategory.FRAMEWORK_INCOMPATIBILITY
        if (self.status is ExecutionStatus.INCOMPATIBLE) != incompatible:
            raise ValueError("framework incompatibility category requires incompatible status")
        if self.status is ExecutionStatus.STEP_LIMIT and (
            self.failure.category is not RuntimeFailureCategory.FRAMEWORK_EXECUTION
        ):
            raise ValueError("step-limit execution requires framework execution failure")

    @classmethod
    def from_run(
        cls,
        *,
        scenario: LabScenario,
        run_config: RuntimeConfig,
        framework_id: FrameworkId,
        framework_version: str,
        trace: TraceIR,
        state: RuntimeState,
        status: ExecutionStatus,
        failure: RuntimeFailure | None,
        started_at: datetime,
        completed_at: datetime,
        provenance: ExecutionProvenance,
    ) -> Self:
        if state.failure != failure:
            raise ValueError("runtime state failure does not match execution failure")
        projected_trace = _project_trace(trace)
        return cls(
            trace=projected_trace,
            trace_sha256=canonical_sha256(projected_trace),
            framework_id=framework_id,
            framework_version=framework_version,
            scenario_id=scenario.scenario_id,
            template_id=scenario.template_id,
            domain=scenario.domain,
            failure_family=scenario.failure_family,
            scenario_input_sha256=canonical_sha256(
                {
                    "user_request": scenario.user_request,
                    "parameters": scenario.parameters,
                    "tool_contract_sha256": scenario.tool_contract_sha256,
                }
            ),
            injection_trigger_sha256=canonical_sha256(scenario.injection),
            terminal_predicate_sha256=canonical_sha256(
                scenario.terminal_predicate_id
            ),
            evidence_selector_sha256=canonical_sha256(
                list(scenario.allowed_evidence_selectors)
            ),
            seed=run_config.seed,
            repetition=run_config.repetition,
            runtime_config=run_config,
            runtime_config_sha256=canonical_sha256(run_config),
            status=status,
            failure=failure,
            started_at=started_at,
            completed_at=completed_at,
            latency_seconds=(completed_at - started_at).total_seconds(),
            steps=state.step,
            tool_calls=state.tool_calls,
            final_message=(
                _sanitize_text(state.final_message)
                if state.final_message is not None
                else None
            ),
            provenance=provenance,
        )


def _is_utc(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() == UTC.utcoffset(value)


def _sanitize_text(value: str) -> str:
    return cast(str, sanitize_diagnostic_value(value))


def _freeze_trace(trace: TraceIR) -> TraceIR:
    frozen_spans = []
    for span in trace.spans:
        frozen_span = span.model_copy(deep=True)
        object.__setattr__(
            frozen_span,
            "attributes",
            _freeze_json_dict(frozen_span.attributes),
        )
        frozen_spans.append(frozen_span)
    frozen_trace = trace.model_copy(deep=True)
    object.__setattr__(frozen_trace, "spans", _FrozenList(frozen_spans))
    return frozen_trace


def _project_trace(trace: TraceIR) -> TraceIR:
    payload = trace.model_dump(mode="python")
    spans = cast(list[dict[str, object]], payload["spans"])
    for span in spans:
        attributes = cast(dict[str, JsonValue], span["attributes"])
        span["attributes"] = {
            key: sanitize_diagnostic_value(value)
            for key, value in attributes.items()
            if key in ALLOWED_ATTRIBUTES
        }
    return TraceIR.model_validate(payload)


def _reject_forbidden_field_names(value: object) -> None:
    if isinstance(value, BaseModel):
        _reject_forbidden_field_names(value.model_dump(mode="python"))
        return
    if isinstance(value, dict):
        for key, item in cast(dict[object, object], value).items():
            if isinstance(key, str) and key.lower() in _FORBIDDEN_RECORD_FIELDS:
                raise ValueError(f"runtime value contains forbidden field: {key}")
            _reject_forbidden_field_names(item)
        return
    if isinstance(value, (list, tuple)):
        for item in cast(list[object] | tuple[object, ...], value):
            _reject_forbidden_field_names(item)
