from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import Field, JsonValue, field_validator

from spanvouch.contracts.versioning import ContractModel, canonical_json
from spanvouch.security.identity import Role


@dataclass(frozen=True)
class AuditEventInput:
    project_id: str
    actor_key_id: str
    action: str
    resource_type: str
    resource_id: str
    result: str
    payload: JsonValue
    occurred_at: datetime
    request_id: str

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("project_id is required")
        if not self.actor_key_id:
            raise ValueError("actor_key_id is required")
        if not self.action:
            raise ValueError("action is required")
        if not self.resource_type:
            raise ValueError("resource_type is required")
        if not self.resource_id:
            raise ValueError("resource_id is required")
        if not self.result:
            raise ValueError("result is required")
        if not self.request_id:
            raise ValueError("request_id is required")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be UTC")


class AuditEvent(ContractModel):
    event_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    event_sequence: int = Field(ge=0)
    previous_event_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor_key_id: str = Field(min_length=1)
    actor_roles: tuple[Role, ...]
    action: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    result: str = Field(min_length=1)
    payload: JsonValue
    request_id: str = Field(min_length=1)
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be UTC")
        return value.astimezone(UTC)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> AuditEvent:
        try:
            payload = json.loads(str(row[11]))
            actor_roles = tuple(Role(item) for item in json.loads(str(row[6])))
            return cls(
                event_id=str(row[0]),
                project_id=str(row[1]),
                event_sequence=int(row[2]),
                previous_event_sha256=(
                    str(row[3]) if row[3] is not None else None
                ),
                event_sha256=str(row[4]),
                actor_key_id=str(row[5]),
                actor_roles=actor_roles,
                action=str(row[7]),
                resource_type=str(row[8]),
                resource_id=str(row[9]),
                result=str(row[10]),
                payload=payload,
                request_id=str(row[12]),
                occurred_at=_parse_timestamp(str(row[13])),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("stored audit event is invalid") from None


class AuditCheckpoint(ContractModel):
    checkpoint_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    first_event_sequence: int = Field(ge=0)
    last_event_sequence: int = Field(ge=0)
    terminal_event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    public_key_pem: bytes = Field(min_length=1)
    signature: bytes = Field(min_length=1)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be UTC")
        return value.astimezone(UTC)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> AuditCheckpoint:
        try:
            return cls(
                checkpoint_id=str(row[0]),
                project_id=str(row[1]),
                first_event_sequence=int(row[2]),
                last_event_sequence=int(row[3]),
                terminal_event_sha256=str(row[4]),
                manifest_sha256=str(row[5]),
                public_key_pem=bytes(row[6]),
                signature=bytes(row[7]),
                created_at=_parse_timestamp(str(row[8])),
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("stored audit checkpoint is invalid") from None


class AuditChain:
    def __init__(self, signing_key: Ed25519PrivateKey | None = None) -> None:
        self._signing_key = signing_key

    def append(self, connection: sqlite3.Connection, event: AuditEventInput) -> AuditEvent:
        if connection.in_transaction:
            return self._append_locked(connection, event)
        connection.execute("BEGIN IMMEDIATE")
        try:
            appended = self._append_locked(connection, event)
            connection.commit()
            return appended
        except Exception:
            connection.rollback()
            raise

    def verify(self, events: Sequence[AuditEvent]) -> None:
        if not events:
            return
        project_id = events[0].project_id
        previous_hash: str | None = None
        for expected_sequence, event in enumerate(events):
            if event.project_id != project_id:
                raise ValueError("audit events must belong to one project")
            if event.event_sequence != expected_sequence:
                raise ValueError("audit event sequence is not contiguous")
            if event.previous_event_sha256 != previous_hash:
                raise ValueError("audit chain hash link is broken")
            expected_hash = self._hash_event(
                project_id=event.project_id,
                event_sequence=event.event_sequence,
                previous_event_sha256=previous_hash,
                actor_key_id=event.actor_key_id,
                actor_roles=event.actor_roles,
                action=event.action,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                result=event.result,
                payload=event.payload,
                request_id=event.request_id,
                occurred_at=event.occurred_at,
            )
            if event.event_sha256 != expected_hash:
                raise ValueError("audit event hash mismatch")
            previous_hash = event.event_sha256

    def create_checkpoint(
        self,
        project_id: str,
        terminal_event: AuditEvent,
        manifest_sha256: str,
    ) -> AuditCheckpoint:
        if self._signing_key is None:
            raise ValueError("audit checkpoint signing key is required")
        if terminal_event.project_id != project_id:
            raise ValueError("terminal event belongs to a different project")
        checkpoint_id = uuid4().hex
        payload = {
            "project_id": project_id,
            "first_event_sequence": 0,
            "last_event_sequence": terminal_event.event_sequence,
            "terminal_event_sha256": terminal_event.event_sha256,
            "manifest_sha256": manifest_sha256,
        }
        payload_bytes = canonical_json(cast(JsonValue, payload)).encode("utf-8")
        signature = self._signing_key.sign(payload_bytes)
        public_key_pem = self._signing_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return AuditCheckpoint(
            checkpoint_id=checkpoint_id,
            project_id=project_id,
            first_event_sequence=0,
            last_event_sequence=terminal_event.event_sequence,
            terminal_event_sha256=terminal_event.event_sha256,
            manifest_sha256=manifest_sha256,
            public_key_pem=public_key_pem,
            signature=signature,
            created_at=terminal_event.occurred_at,
        )

    def _append_locked(
        self, connection: sqlite3.Connection, event: AuditEventInput
    ) -> AuditEvent:
        actor_roles = self._actor_roles(connection, event.actor_key_id, event.project_id)
        previous_row = connection.execute(
            "SELECT event_sha256 FROM audit_events "
            "WHERE project_id = ? ORDER BY event_sequence DESC LIMIT 1",
            (event.project_id,),
        ).fetchone()
        previous_event_sha256 = str(previous_row[0]) if previous_row is not None else None
        sequence_row = connection.execute(
            "SELECT COALESCE(MAX(event_sequence), -1) + 1 AS next_sequence "
            "FROM audit_events WHERE project_id = ?",
            (event.project_id,),
        ).fetchone()
        if sequence_row is None:
            raise ValueError("audit sequence allocation failed")
        event_sequence = int(sequence_row[0])
        event_sha256 = self._hash_event(
            project_id=event.project_id,
            event_sequence=event_sequence,
            previous_event_sha256=previous_event_sha256,
            actor_key_id=event.actor_key_id,
            actor_roles=actor_roles,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            result=event.result,
            payload=event.payload,
            request_id=event.request_id,
            occurred_at=event.occurred_at,
        )
        payload_json = canonical_json(event.payload)
        event_id = uuid4().hex
        connection.execute(
            "INSERT INTO audit_events("
            "event_id, project_id, event_sequence, previous_event_sha256, event_sha256, "
            "actor_key_id, actor_roles_json, action, resource_type, resource_id, result, "
            "payload_json, request_id, occurred_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                event.project_id,
                event_sequence,
                previous_event_sha256,
                event_sha256,
                event.actor_key_id,
                canonical_json([role.value for role in actor_roles]),
                event.action,
                event.resource_type,
                event.resource_id,
                event.result,
                payload_json,
                event.request_id,
                _timestamp(event.occurred_at),
            ),
        )
        return AuditEvent(
            event_id=event_id,
            project_id=event.project_id,
            event_sequence=event_sequence,
            previous_event_sha256=previous_event_sha256,
            event_sha256=event_sha256,
            actor_key_id=event.actor_key_id,
            actor_roles=actor_roles,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            result=event.result,
            payload=json.loads(payload_json),
            request_id=event.request_id,
            occurred_at=event.occurred_at.astimezone(UTC),
        )

    @staticmethod
    def _actor_roles(
        connection: sqlite3.Connection, actor_key_id: str, project_id: str
    ) -> tuple[Role, ...]:
        row = connection.execute(
            "SELECT project_id, roles_json FROM api_keys WHERE key_id = ?",
            (actor_key_id,),
        ).fetchone()
        if row is None:
            raise ValueError("audit actor key is missing")
        actor_project_id = row[0]
        if actor_project_id is not None and str(actor_project_id) != project_id:
            raise ValueError("audit actor key project mismatch")
        try:
            roles = json.loads(str(row[1]))
            return tuple(Role(item) for item in roles)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("stored audit actor roles are invalid") from None

    @staticmethod
    def _hash_event(
        *,
        project_id: str,
        event_sequence: int,
        previous_event_sha256: str | None,
        actor_key_id: str,
        actor_roles: Sequence[Role],
        action: str,
        resource_type: str,
        resource_id: str,
        result: str,
        payload: JsonValue,
        request_id: str,
        occurred_at: datetime,
    ) -> str:
        payload_data = {
            "project_id": project_id,
            "event_sequence": event_sequence,
            "previous_event_sha256": previous_event_sha256,
            "actor_key_id": actor_key_id,
            "actor_roles": [role.value for role in actor_roles],
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "result": result,
            "payload": payload,
            "request_id": request_id,
            "occurred_at": occurred_at,
        }
        return sha256(
            canonical_json(cast(JsonValue, payload_data)).encode("utf-8")
        ).hexdigest()


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be UTC")
    return parsed.astimezone(UTC)
