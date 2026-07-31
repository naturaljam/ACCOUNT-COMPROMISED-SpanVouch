"""Schema-bound normalization for model responses before persistence."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from spanvouch.contracts.credentials import contains_credential_signature
from spanvouch.contracts.sanitization import sanitize_diagnostic_value
from spanvouch.contracts.versioning import canonical_json


class ProviderContentDisposition(StrEnum):
    ACCEPTED = "accepted"
    NORMALIZED_INVALID = "normalized_invalid"


class NormalizedProviderContent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str = Field(min_length=1)
    disposition: ProviderContentDisposition


class ProviderResponseContentPolicy(Protocol):
    def normalize(self, content: str) -> NormalizedProviderContent:
        """Return canonical, persistence-safe consumer content."""


class JsonModelResponseContentPolicy:
    """Validate a provider JSON draft before it reaches the provider cache."""

    def __init__(self, schema: type[BaseModel]) -> None:
        self._schema = schema

    def normalize(self, content: str) -> NormalizedProviderContent:
        try:
            if contains_credential_signature(content):
                return self._invalid()
            payload = cast(JsonValue, json.loads(content))
            sanitized = sanitize_diagnostic_value(payload)
            if sanitized != payload:
                return self._invalid()
            validated = self._schema.model_validate(sanitized)
            canonical = canonical_json(
                cast(JsonValue, validated.model_dump(mode="json"))
            )
        except (RecursionError, TypeError, ValueError, UnicodeError):
            return self._invalid()
        return NormalizedProviderContent(
            content=canonical,
            disposition=ProviderContentDisposition.ACCEPTED,
        )

    @staticmethod
    def _invalid() -> NormalizedProviderContent:
        return NormalizedProviderContent(
            content="{}",
            disposition=ProviderContentDisposition.NORMALIZED_INVALID,
        )
