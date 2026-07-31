from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ConfigDict, Field

from spanvouch.diagnosis.response_content import (
    JsonModelResponseContentPolicy,
    NormalizedProviderContent,
    ProviderContentDisposition,
)


class Draft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: str = Field(min_length=1)


def test_schema_policy_accepts_legitimate_long_identifier() -> None:
    policy = JsonModelResponseContentPolicy(Draft)
    long_identifier = (
        "degradation_result_remained_missing_after_dependency_call"
    )

    normalized = policy.normalize(json.dumps({"statement": long_identifier}))

    assert normalized.disposition is ProviderContentDisposition.ACCEPTED
    assert json.loads(normalized.content) == {"statement": long_identifier}


def test_schema_policy_canonicalizes_valid_json() -> None:
    policy = JsonModelResponseContentPolicy(Draft)

    normalized = policy.normalize(' { "statement" : "safe" } ')

    assert normalized == NormalizedProviderContent(
        content='{"statement":"safe"}',
        disposition=ProviderContentDisposition.ACCEPTED,
    )


@pytest.mark.parametrize(
    "content",
    (
        "not-json",
        "[]",
        '{"statement":"safe","extra":true}',
        '{"statement":""}',
        "null",
    ),
)
def test_schema_policy_normalizes_invalid_content(content: str) -> None:
    normalized = JsonModelResponseContentPolicy(Draft).normalize(content)

    assert normalized == NormalizedProviderContent(
        content="{}",
        disposition=ProviderContentDisposition.NORMALIZED_INVALID,
    )


@pytest.mark.parametrize(
    "unsafe",
    (
        "Authorization: Bearer stolen-provider-secret",
        "api_" + "key=" + "sk" + "-" + "1234567890abcdefghijklmnop",
        "session_token: abcdefghijklmnopqrstuvwxyz012345",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
        "-----BEGIN PRIVATE KEY----- secret material",
    ),
)
def test_schema_policy_never_returns_credential_like_content(unsafe: str) -> None:
    normalized = JsonModelResponseContentPolicy(Draft).normalize(
        json.dumps({"statement": unsafe})
    )

    assert normalized.content == "{}"
    assert normalized.disposition is ProviderContentDisposition.NORMALIZED_INVALID
    assert unsafe not in normalized.model_dump_json()


def test_schema_policy_fails_closed_on_excessive_nesting() -> None:
    nested: object = "safe"
    for _ in range(80):
        nested = {"statement": nested}

    normalized = JsonModelResponseContentPolicy(Draft).normalize(json.dumps(nested))

    assert normalized.content == "{}"
    assert normalized.disposition is ProviderContentDisposition.NORMALIZED_INVALID
