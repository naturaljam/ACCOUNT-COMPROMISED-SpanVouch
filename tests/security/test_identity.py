from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from spanvouch.security.identity import (
    ApiKeyMaterial,
    AuthenticationError,
    Role,
    parse_bearer_token,
)

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def test_created_project_key_verifies_without_persisting_plaintext() -> None:
    record, secret = ApiKeyMaterial.create(
        project_id="project-a",
        roles=(Role.OPERATOR,),
        now=NOW,
    )

    assert secret.startswith(f"svk_{record.key_id}_")
    assert record.project_id == "project-a"
    assert record.roles == (Role.OPERATOR,)
    assert record.secret_digest != secret.encode("utf-8")
    assert record.secret_salt
    assert secret not in repr(record)
    assert ApiKeyMaterial.verify(secret, record, now=NOW) is True


def test_key_verification_rejects_wrong_expired_and_revoked_credentials() -> None:
    record, secret = ApiKeyMaterial.create(
        project_id="project-a",
        roles=(Role.REVIEWER,),
        now=NOW,
        expires_at=NOW + timedelta(minutes=1),
    )
    wrong_secret = f"{secret[:-1]}x" if secret[-1] != "x" else f"{secret[:-1]}y"

    assert ApiKeyMaterial.verify(wrong_secret, record, now=NOW) is False
    assert (
        ApiKeyMaterial.verify(record=record, presented=secret, now=NOW + timedelta(minutes=2))
        is False
    )
    assert (
        ApiKeyMaterial.verify(
            secret,
            replace(record, revoked_at=NOW + timedelta(seconds=1)),
            now=NOW + timedelta(seconds=2),
        )
        is False
    )


def test_system_admin_key_is_the_only_system_scoped_key() -> None:
    admin_record, _ = ApiKeyMaterial.create(
        project_id=None,
        roles=(Role.ADMIN,),
        now=NOW,
    )

    assert admin_record.project_id is None
    assert admin_record.roles == (Role.ADMIN,)
    with pytest.raises(ValueError, match="system-scoped"):
        ApiKeyMaterial.create(project_id=None, roles=(Role.VIEWER,), now=NOW)
    with pytest.raises(ValueError, match="project-scoped"):
        ApiKeyMaterial.create(project_id="project-a", roles=(Role.ADMIN,), now=NOW)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer svk_key_secret", "svk_key_secret"),
        ("bearer svk_key_secret", "svk_key_secret"),
    ],
)
def test_parse_bearer_token_returns_only_well_formed_bearer_values(
    header: str, expected: str
) -> None:
    assert parse_bearer_token(header) == expected


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer", "Bearer   "])
def test_parse_bearer_token_rejects_missing_or_malformed_values(header: str | None) -> None:
    with pytest.raises(AuthenticationError, match="bearer token"):
        parse_bearer_token(header)
