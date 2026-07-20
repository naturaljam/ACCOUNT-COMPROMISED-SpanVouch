from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Protocol, Self, cast, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from spanvouch.contracts.diagnosis import DiagnosisExecution, DiagnosisReport, ProviderUsage
from spanvouch.contracts.trace import DiagnosticContext
from spanvouch.trace.evidence_catalog import EvidenceCatalog

if TYPE_CHECKING:
    from spanvouch.contracts.verification import EvidenceGap


class Diagnoser(Protocol):
    kind: str
    version_fingerprint: str

    async def diagnose(
        self, context: DiagnosticContext, evidence: EvidenceCatalog
    ) -> DiagnosisExecution:
        raise NotImplementedError


@runtime_checkable
class RevisionCapableDiagnoser(Diagnoser, Protocol):
    async def revise(
        self,
        context: DiagnosticContext,
        evidence: EvidenceCatalog,
        previous_report: DiagnosisReport,
        evidence_gaps: tuple[EvidenceGap, ...],
    ) -> DiagnosisExecution:
        raise NotImplementedError


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


def _immutable_json_error() -> NoReturn:
    raise TypeError("generation JSON is immutable")


class _FrozenJsonDict(dict[str, JsonValue]):
    def __init__(self, values: Mapping[str, JsonValue]) -> None:
        dict.__init__(self, {key: _freeze_json(value) for key, value in values.items()})

    def __setitem__(self, key: str, value: JsonValue) -> None:
        _immutable_json_error()

    def __delitem__(self, key: str) -> None:
        _immutable_json_error()

    def __ior__(self, other: Any) -> Any:  # type: ignore[misc]
        _immutable_json_error()

    def clear(self) -> None:
        _immutable_json_error()

    def pop(self, key: Any, default: Any = None) -> Any:
        _immutable_json_error()

    def popitem(self) -> NoReturn:
        _immutable_json_error()

    def setdefault(self, key: Any, default: Any = None) -> Any:
        _immutable_json_error()

    def update(self, other: Any = (), /, **kwargs: Any) -> None:
        _immutable_json_error()


class _FrozenJsonList(list[JsonValue]):
    def __init__(self, values: Iterable[JsonValue]) -> None:
        list.__init__(self, (_freeze_json(value) for value in values))

    def __setitem__(self, index: Any, value: Any) -> None:
        _immutable_json_error()

    def __delitem__(self, index: Any) -> None:
        _immutable_json_error()

    def __iadd__(self, values: Any) -> Any:  # type: ignore[misc]
        _immutable_json_error()

    def __imul__(self, count: Any) -> Any:  # type: ignore[misc]
        _immutable_json_error()

    def append(self, value: JsonValue) -> None:
        _immutable_json_error()

    def clear(self) -> None:
        _immutable_json_error()

    def extend(self, values: Iterable[JsonValue]) -> None:
        _immutable_json_error()

    def insert(self, index: Any, value: JsonValue) -> None:
        _immutable_json_error()

    def pop(self, index: Any = -1) -> Any:
        _immutable_json_error()

    def remove(self, value: JsonValue) -> None:
        _immutable_json_error()

    def reverse(self) -> None:
        _immutable_json_error()

    def sort(self, *, key: object = None, reverse: bool = False) -> None:
        _immutable_json_error()


def _freeze_json(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return cast(JsonValue, _FrozenJsonDict(value))
    if isinstance(value, list):
        return cast(JsonValue, _FrozenJsonList(value))
    return value


class GenerationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = "deepseek-v4-flash"
    max_tokens: int = Field(default=1200, ge=1, le=4096)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    extra_body: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_and_freeze_extra_body(self) -> Self:
        reserved = {
            "model",
            "messages",
            "stream",
            "response_format",
            "max_tokens",
            "temperature",
        }
        collisions = reserved.intersection(self.extra_body)
        if collisions:
            raise ValueError(f"extra_body contains reserved keys: {', '.join(sorted(collisions))}")
        object.__setattr__(self, "extra_body", _FrozenJsonDict(self.extra_body))
        return self


class ProviderResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str
    model: str
    response_id: str
    finish_reason: str
    usage: ProviderUsage


class ModelProvider(Protocol):
    async def complete(
        self,
        messages: tuple[ChatMessage, ...],
        config: GenerationConfig,
    ) -> ProviderResponse:
        raise NotImplementedError
