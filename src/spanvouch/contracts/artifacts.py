from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from spanvouch.contracts.versioning import (
    IDENTIFIER_PATTERN,
    SHA256_PATTERN,
    ContractModel,
    ContractRoot,
)

_GIT_COMMIT_PATTERN = r"^[0-9a-f]{40}$"
_CURRENCY_PATTERN = r"^[A-Z]{3}$"


def _require_sorted_unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{field_name} must be sorted and unique")
    return values


def _artifact_identities(
    values: tuple[ArtifactRef, ...], field_name: str
) -> tuple[ArtifactRef, ...]:
    identities = tuple(reference.path for reference in values)
    _require_sorted_unique(identities, field_name)
    return values


class ArtifactRef(ContractModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    media_type: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def validate_relative_posix_path(cls, value: str) -> str:
        if value.startswith("/") or "\\" in value or ":" in value or any(
            segment in {"", ".", ".."} for segment in value.split("/")
        ):
            raise ValueError("artifact path must be a relative POSIX path")
        return value


class CodeProvenance(ContractModel):
    git_commit: str = Field(pattern=_GIT_COMMIT_PATTERN)
    repository_identity: str = Field(min_length=1)
    dirty_worktree: bool


class PackageProvenance(ContractModel):
    name: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)
    version: str = Field(min_length=1)


class DatasetProvenance(ContractModel):
    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    payloads: tuple[ArtifactRef, ...] = ()

    @field_validator("payloads")
    @classmethod
    def validate_payloads(cls, values: tuple[ArtifactRef, ...]) -> tuple[ArtifactRef, ...]:
        return _artifact_identities(values, "payloads")


class RandomnessProvenance(ContractModel):
    seed: int
    deterministic_flags: tuple[str, ...] = ()

    @field_validator("deterministic_flags")
    @classmethod
    def validate_deterministic_flags(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_sorted_unique(values, "deterministic_flags")


class ModelProvenance(ContractModel):
    provider: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)
    model: str = Field(min_length=1)
    endpoint_class: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)
    generation_config_sha256: str = Field(pattern=SHA256_PATTERN)
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)


class RuntimeProvenance(ContractModel):
    python: str = Field(min_length=1)
    os: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    dependency_lock_sha256: str = Field(pattern=SHA256_PATTERN)


class UsageProvenance(ContractModel):
    requests: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_total_tokens(self) -> Self:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens plus output_tokens")
        return self


class CostProvenance(ContractModel):
    currency: str = Field(pattern=_CURRENCY_PATTERN)
    basis: Literal["estimated", "billed"]
    amount: float | None = Field(default=None, ge=0.0)
    pricing_ref: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_pricing(self) -> Self:
        if self.amount is None and self.pricing_ref is not None:
            raise ValueError("pricing_ref requires an amount")
        if self.amount is not None and self.pricing_ref is None:
            raise ValueError("amount requires a pricing_ref")
        return self


class ArtifactManifest(ContractRoot):
    schema_name: Literal["spanvouch.artifact-manifest"] = "spanvouch.artifact-manifest"
    schema_version: Literal["1.0"] = "1.0"
    artifact_id: str = Field(min_length=1)
    artifact_kind: str = Field(min_length=1)
    created_at_utc: datetime
    command_name: str = Field(min_length=1)
    code: CodeProvenance
    package: PackageProvenance
    contracts: dict[str, str] = Field(min_length=1)
    datasets: tuple[DatasetProvenance, ...] = ()
    configuration: ArtifactRef
    randomness: RandomnessProvenance | None = None
    models: tuple[ModelProvenance, ...] = ()
    runtime: RuntimeProvenance
    inputs: tuple[ArtifactRef, ...] = ()
    outputs: tuple[ArtifactRef, ...] = Field(min_length=1)
    metrics_schema_ref: str | None = None
    usage: UsageProvenance | None = None
    cost: CostProvenance | None = None
    parent_artifacts: tuple[str, ...] = ()
    provider_status: Literal["not_used", "used", "failed"]

    @field_validator("created_at_utc")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("created_at_utc must be UTC")
        return value

    @field_validator("contracts")
    @classmethod
    def validate_contracts(cls, values: dict[str, str]) -> dict[str, str]:
        if list(values) != sorted(values) or any(not value for value in values.values()):
            raise ValueError("contracts must be sorted and unique")
        return values

    @field_validator("inputs", "outputs")
    @classmethod
    def validate_artifact_references(
        cls, values: tuple[ArtifactRef, ...], info: object
    ) -> tuple[ArtifactRef, ...]:
        field_name = getattr(info, "field_name", "artifact references")
        return _artifact_identities(values, field_name)

    @field_validator("parent_artifacts")
    @classmethod
    def validate_parent_artifacts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _require_sorted_unique(values, "parent_artifacts")

    @model_validator(mode="after")
    def validate_provider_provenance(self) -> Self:
        if self.provider_status == "not_used" and (
            self.models or self.usage is not None or self.cost is not None
        ):
            raise ValueError("not_used provider forbids model usage and cost")
        if self.provider_status == "used" and (not self.models or self.usage is None):
            raise ValueError("used provider requires models and usage")
        return self

    def require_release_evidence(self) -> None:
        if self.code.dirty_worktree:
            raise ValueError("release evidence requires a clean worktree")
