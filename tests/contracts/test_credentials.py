from __future__ import annotations

import pytest

from spanvouch.contracts.credentials import contains_credential_signature


@pytest.mark.parametrize(
    "value",
    (
        "Authorization: Bearer provider-test-sentinel",
        "api_" + "key=" + "sk" + "-" + "provider-test-sentinel-1234567890",
        "session_token: provider-test-sentinel-1234567890",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
        "-----BEGIN PRIVATE KEY----- test material",
        "https://user:password@example.invalid/resource",
    ),
)
def test_contains_credential_signature_detects_non_bypassable_forms(
    value: str,
) -> None:
    assert contains_credential_signature(value) is True


@pytest.mark.parametrize(
    "value",
    (
        "degradation_result_remained_missing_after_dependency_call",
        "https://example.invalid/resource",
    ),
)
def test_contains_credential_signature_allows_safe_prose(value: str) -> None:
    assert contains_credential_signature(value) is False
