from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import uuid4


class AuthenticationError(ValueError):
    """Raised when an HTTP authorization value cannot be authenticated."""


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


@dataclass(frozen=True)
class Principal:
    key_id: str
    project_id: str | None
    roles: tuple[Role, ...]

    def __post_init__(self) -> None:
        roles = _validated_roles(self.project_id, self.roles)
        object.__setattr__(self, "roles", roles)

    @property
    def is_system_admin(self) -> bool:
        return self.project_id is None


@dataclass(frozen=True)
class ApiKeyRecord:
    key_id: str
    prefix: str
    project_id: str | None
    roles: tuple[Role, ...]
    secret_salt: bytes
    secret_digest: bytes
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        roles = _validated_roles(self.project_id, self.roles)
        object.__setattr__(self, "roles", roles)
        if not self.key_id or self.prefix != f"svk_{self.key_id}":
            raise ValueError("api key record has an invalid prefix")
        if len(self.secret_salt) != 16:
            raise ValueError("api key record requires a 16-byte salt")
        if len(self.secret_digest) != 32:
            raise ValueError("api key record requires a 32-byte digest")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("api key expiry must be after creation")


class ApiKeyMaterial:
    """Creates and verifies API key material without retaining plaintext secrets."""

    @staticmethod
    def create(
        *,
        project_id: str | None,
        roles: tuple[Role, ...],
        now: datetime,
        expires_at: datetime | None = None,
    ) -> tuple[ApiKeyRecord, str]:
        validated_roles = _validated_roles(project_id, roles)
        key_id = uuid4().hex
        secret = secrets.token_urlsafe(32)
        salt = secrets.token_bytes(16)
        record = ApiKeyRecord(
            key_id=key_id,
            prefix=f"svk_{key_id}",
            project_id=project_id,
            roles=validated_roles,
            secret_salt=salt,
            secret_digest=_digest(secret, salt),
            created_at=now,
            expires_at=expires_at,
        )
        return record, f"{record.prefix}_{secret}"

    @staticmethod
    def verify(presented: str, record: ApiKeyRecord, *, now: datetime) -> bool:
        try:
            key_id, secret = _parse_api_key(presented)
        except AuthenticationError:
            return False
        if key_id != record.key_id:
            return False
        if record.revoked_at is not None and now >= record.revoked_at:
            return False
        if record.expires_at is not None and now >= record.expires_at:
            return False
        return hmac.compare_digest(_digest(secret, record.secret_salt), record.secret_digest)


def parse_bearer_token(header: str | None) -> str:
    if header is None:
        raise AuthenticationError("bearer token is required")
    parts = header.split()
    if len(parts) != 2 or parts[0].casefold() != "bearer" or not parts[1].startswith("svk_"):
        raise AuthenticationError("bearer token is malformed")
    return parts[1]


def _parse_api_key(value: str) -> tuple[str, str]:
    prefix, key_id, secret = value.split("_", maxsplit=2)
    if prefix != "svk" or not key_id or not secret:
        raise AuthenticationError("api key is malformed")
    return key_id, secret


def _digest(secret: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        secret.encode("utf-8"),
        salt=salt,
        n=16_384,
        r=8,
        p=1,
        dklen=32,
    )


def _validated_roles(project_id: str | None, roles: tuple[Role, ...]) -> tuple[Role, ...]:
    normalized = tuple(Role(role) for role in roles)
    if not normalized or len(set(normalized)) != len(normalized):
        raise ValueError("api key roles must be non-empty and unique")
    if project_id is None:
        if normalized != (Role.ADMIN,):
            raise ValueError("system-scoped keys require only the admin role")
        return normalized
    if not project_id:
        raise ValueError("project-scoped keys require a project id")
    if Role.ADMIN in normalized:
        raise ValueError("project-scoped keys cannot include the admin role")
    return normalized
