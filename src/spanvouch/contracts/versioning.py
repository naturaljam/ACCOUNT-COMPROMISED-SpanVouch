from __future__ import annotations

import json
from contextlib import suppress
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue


class ContractError(ValueError):
    """Base error for contract compatibility and canonicalization failures."""


class UnknownSchemaError(ContractError):
    """Raised when a schema name is not registered."""


class UnsupportedSchemaVersionError(ContractError):
    """Raised when a known schema version is not supported."""


class ContractIntegrityError(ContractError):
    """Raised when canonical bytes do not match their expected identity."""


class ContractModel(BaseModel):
    """Immutable base for strict contract payloads."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ContractRoot(ContractModel):
    """Contract root carrying an explicit schema identity and version."""

    schema_name: str
    schema_version: str


def _canonical_value(value: object) -> JsonValue:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python"))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ContractError("canonical timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        canonical: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError("canonical JSON objects require string keys")
            canonical[key] = _canonical_value(item)
        return canonical
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ContractError("NaN and Infinity are not canonical JSON")
        return value
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise ContractError(f"unsupported canonical JSON value: {type(value).__name__}")


def _canonical_root(value: BaseModel | JsonValue) -> JsonValue:
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        # Phase 3 review snapshots pass object/array JSON documents as strings,
        # while evidence values may legitimately be JSON-looking scalar strings.
        with suppress(json.JSONDecodeError):
            value = cast(JsonValue, json.loads(value))
    return _canonical_value(value)


def canonical_bytes(value: BaseModel | JsonValue) -> bytes:
    """Return deterministic UTF-8 JSON bytes for a model or JSON value."""
    return json.dumps(
        _canonical_root(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json(value: BaseModel | JsonValue) -> str:
    """Return the canonical JSON text represented by ``value``."""
    return canonical_bytes(value).decode("utf-8")


def canonical_sha256(
    value: BaseModel | JsonValue,
    *,
    expected_sha256: str | None = None,
) -> str:
    """Hash canonical bytes and optionally verify an expected digest."""
    digest = sha256(canonical_bytes(value)).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ContractIntegrityError("canonical SHA-256 mismatch")
    return digest


def require_schema(
    schema_name: str,
    schema_version: str,
    *,
    supported: dict[str, set[str]],
) -> None:
    """Require an exact registered schema name and version."""
    versions = supported.get(schema_name)
    if versions is None:
        raise UnknownSchemaError(f"unknown schema: {schema_name}")
    if schema_version not in versions:
        raise UnsupportedSchemaVersionError(
            f"unsupported schema version: {schema_name}/{schema_version}"
        )
