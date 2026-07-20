"""Immutable configuration and preregistration freeze rules for Phase 5."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, NoReturn, Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from spanvouch.contracts.versioning import SHA256_PATTERN, canonical_sha256


def _immutable_json_error() -> NoReturn:
    raise TypeError("configuration JSON is immutable")


class _FrozenJsonDict(dict[str, JsonValue]):
    """A dict-shaped JSON value which rejects all public mutation methods."""

    def __init__(self, values: Mapping[str, JsonValue]) -> None:
        dict.__init__(
            self,
            {key: _freeze_json_value(value) for key, value in values.items()},
        )

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

    def update(
        self,
        other: Any = (),
        /,
        **kwargs: Any,
    ) -> None:
        _immutable_json_error()


class _FrozenJsonList(list[JsonValue]):
    """A list-shaped JSON value which rejects all public mutation methods."""

    def __init__(self, values: Iterable[JsonValue]) -> None:
        list.__init__(self, (_freeze_json_value(value) for value in values))

    def __setitem__(
        self,
        index: Any,
        value: Any,
    ) -> None:
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


def _freeze_json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return cast(JsonValue, _FrozenJsonDict(value))
    if isinstance(value, list):
        return cast(JsonValue, _FrozenJsonList(value))
    return value


class ExperimentMode(StrEnum):
    """Whether a configuration is for the pilot or the formal experiment."""

    PILOT = "pilot"
    FORMAL = "formal"


class ConditionId(StrEnum):
    """The six preregistered Phase 5 verification conditions."""

    B0 = "b0_no_verifier"
    B1 = "b1_deterministic"
    B2 = "b2_deepseek_shared"
    B3 = "b3_deepseek_isolated"
    B4 = "b4_qwen_isolated"
    B5 = "b5_deterministic_qwen"


class ModelEndpointConfig(BaseModel):
    """The fixed request-shaping parameters for one model endpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    endpoint_class: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    max_tokens: int = Field(ge=1, le=4096)
    temperature: float = Field(ge=0.0, le=2.0)
    extra_body: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def freeze_extra_body(self) -> Self:
        """Prevent a formal hash from being invalidated through nested mutation."""
        object.__setattr__(self, "extra_body", _FrozenJsonDict(self.extra_body))
        return self


class BudgetPolicy(BaseModel):
    """The non-negotiable Phase 5 expenditure stop rules."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    monthly_cap_cny: Decimal = Field(gt=0)
    pilot_fraction: Decimal = Field(gt=0, le=Decimal("0.10"))
    stop_fraction: Decimal = Field(gt=0, le=Decimal("0.80"))


class PricingFileProvenance(BaseModel):
    """Immutable, credential-free identity of one canonical pricing file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    canonical_sha256: str = Field(pattern=SHA256_PATTERN)
    source_url: str = Field(min_length=1)
    effective_date: date
    currency: Literal["CNY"]


class GpuLeaseApproval(BaseModel):
    """Frozen maximum and static identity for one approved Qwen GPU lease."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cloud_provider: str = Field(min_length=1)
    region: str = Field(min_length=1)
    instance_type: str = Field(min_length=1)
    maximum_hours: Decimal = Field(gt=0)
    maximum_cost_cny: Decimal = Field(gt=0)


class EndpointDeploymentProvenance(BaseModel):
    """Sanitized endpoint, deployment and pricing identity frozen before a run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Literal["deepseek", "qwen"]
    model: str = Field(min_length=1)
    endpoint_class: str = Field(min_length=1)
    base_url_sha256: str = Field(pattern=SHA256_PATTERN)
    pricing: PricingFileProvenance
    container_repo_digest: str | None = None
    hf_revision: str | None = None
    chat_template_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    dtype: str | None = Field(default=None, min_length=1)
    max_model_len: int | None = Field(default=None, ge=1)
    gpu_lease_approval: GpuLeaseApproval | None = None

    @model_validator(mode="after")
    def require_provider_specific_pins(self) -> Self:
        """Require the full self-hosted serving identity for Qwen experiments."""
        if self.provider == "qwen":
            required = {
                "container_repo_digest": self.container_repo_digest,
                "hf_revision": self.hf_revision,
                "chat_template_sha256": self.chat_template_sha256,
                "dtype": self.dtype,
                "max_model_len": self.max_model_len,
                "gpu_lease_approval": self.gpu_lease_approval,
            }
            missing = next((name for name, value in required.items() if value is None), None)
            if missing is not None:
                raise ValueError(f"qwen live provenance requires {missing}")
            container_repo_digest = self.container_repo_digest
            hf_revision = self.hf_revision
            assert container_repo_digest is not None and hf_revision is not None
            if not container_repo_digest.startswith("vllm/vllm-openai@sha256:") or (
                len(container_repo_digest) != len("vllm/vllm-openai@sha256:") + 64
            ):
                raise ValueError("container_repo_digest must be a full vLLM RepoDigest")
            if len(hf_revision) != 40:
                raise ValueError("hf_revision must be an exact 40-character revision")
        return self

    @property
    def sha256(self) -> str:
        return canonical_sha256(cast(JsonValue, self.model_dump(mode="json")))


class LiveDeploymentProvenance(BaseModel):
    """The complete approved live provider identity for Phase 5."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deepseek: EndpointDeploymentProvenance
    qwen: EndpointDeploymentProvenance

    @model_validator(mode="after")
    def validate_roles(self) -> Self:
        if self.deepseek.provider != "deepseek" or self.qwen.provider != "qwen":
            raise ValueError("live provenance provider roles are invalid")
        return self


class Phase5ExperimentConfig(BaseModel):
    """A validated, immutable Phase 5 experiment configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str = Field(pattern=r"^phase5-[a-z0-9-]+$")
    mode: ExperimentMode
    seed: int
    repetitions: int = Field(ge=3, le=20)
    conditions: tuple[ConditionId, ...]
    frameworks: tuple[Literal["langgraph", "autogen"], ...]
    generator: ModelEndpointConfig
    shared_verifier: ModelEndpointConfig
    isolated_verifier: ModelEndpointConfig
    cross_model_verifier: ModelEndpointConfig
    budget: BudgetPolicy
    live_provenance: LiveDeploymentProvenance
    coverage_loss_tolerance: float | None = Field(default=None, ge=0.0, le=0.10)
    frozen_at_utc: datetime | None = None
    config_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_preregistered_fields(self) -> Self:
        """Keep the comparison matrix complete and formal runs self-authenticating."""
        if len(self.conditions) != len(ConditionId) or set(self.conditions) != set(ConditionId):
            raise ValueError("configuration must contain all six conditions exactly once")
        if len(self.frameworks) != 2 or set(self.frameworks) != {"langgraph", "autogen"}:
            raise ValueError("configuration must contain both frameworks exactly once")
        endpoint_pairs = (
            (self.generator, self.live_provenance.deepseek),
            (self.shared_verifier, self.live_provenance.deepseek),
            (self.isolated_verifier, self.live_provenance.deepseek),
            (self.cross_model_verifier, self.live_provenance.qwen),
        )
        for endpoint, provenance in endpoint_pairs:
            if (endpoint.provider, endpoint.model, endpoint.endpoint_class) != (
                provenance.provider,
                provenance.model,
                provenance.endpoint_class,
            ):
                raise ValueError("live provenance does not match configured endpoint")

        non_thinking = {"thinking": {"type": "disabled"}}
        for endpoint in (
            self.generator,
            self.shared_verifier,
            self.isolated_verifier,
        ):
            if endpoint.extra_body != non_thinking:
                raise ValueError("DeepSeek thinking must be explicitly disabled")

        if self.mode is ExperimentMode.PILOT:
            if self.repetitions != 3:
                raise ValueError("pilot configuration must use exactly three repetitions")
            if self.coverage_loss_tolerance is not None:
                raise ValueError("pilot configuration must not set coverage_loss_tolerance")
            if self.frozen_at_utc is not None or self.config_sha256 is not None:
                raise ValueError("pilot configuration must not be frozen")
            return self

        if (
            self.frozen_at_utc is None
            or self.coverage_loss_tolerance is None
            or self.config_sha256 is None
        ):
            raise ValueError("formal configuration must be frozen")
        if self.repetitions < 5:
            raise ValueError("formal configuration must use at least five repetitions")
        if self.frozen_at_utc.utcoffset() != UTC.utcoffset(self.frozen_at_utc):
            raise ValueError("formal configuration must have a UTC frozen_at_utc")

        payload = cast(
            JsonValue,
            self.model_dump(mode="json", exclude={"config_sha256"}),
        )
        if canonical_sha256(payload) != self.config_sha256:
            raise ValueError("config_sha256 does not match formal configuration")
        return self


class FormalFreezePolicy(BaseModel):
    """Policy constraints selected before the formal experiment is frozen."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    minimum_repetitions: int = Field(ge=5, le=20)
    maximum_repetitions: int = Field(ge=5, le=20)
    maximum_coverage_loss: float = Field(ge=0.0, le=0.10)
    required_confidence_level: float = Field(gt=0.0, lt=1.0)
    bootstrap_draws: int = Field(ge=1)
    multiple_comparison_correction: Literal["holm"]

    @model_validator(mode="after")
    def validate_repetition_bounds(self) -> Self:
        """Require a non-empty permitted repetition range."""
        if self.minimum_repetitions > self.maximum_repetitions:
            raise ValueError("minimum_repetitions cannot exceed maximum_repetitions")
        return self


def load_experiment_config(path: Path) -> Phase5ExperimentConfig:
    """Load a checked-in Phase 5 configuration without accepting unknown fields."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return Phase5ExperimentConfig.model_validate(payload)


def freeze_formal_config(
    pilot_config: Phase5ExperimentConfig,
    policy: FormalFreezePolicy,
    *,
    repetitions: int,
    coverage_loss_tolerance: float,
    frozen_at_utc: datetime,
) -> Phase5ExperimentConfig:
    """Create a complete, self-hashed formal configuration from pilot analysis choices."""
    if pilot_config.mode is not ExperimentMode.PILOT:
        raise ValueError("only a pilot configuration can be frozen")
    if not policy.minimum_repetitions <= repetitions <= policy.maximum_repetitions:
        raise ValueError("repetitions must satisfy the formal freeze policy")
    if not 0.0 <= coverage_loss_tolerance <= policy.maximum_coverage_loss:
        raise ValueError("coverage_loss_tolerance must satisfy the formal freeze policy")
    if frozen_at_utc.utcoffset() != UTC.utcoffset(frozen_at_utc):
        raise ValueError("frozen_at_utc must be UTC")

    payload = pilot_config.model_dump(mode="json")
    payload.update(
        mode=ExperimentMode.FORMAL.value,
        repetitions=repetitions,
        coverage_loss_tolerance=coverage_loss_tolerance,
        frozen_at_utc=frozen_at_utc,
        config_sha256=None,
    )
    payload["config_sha256"] = canonical_sha256(
        cast(JsonValue, {key: value for key, value in payload.items() if key != "config_sha256"})
    )
    return Phase5ExperimentConfig.model_validate(payload)
