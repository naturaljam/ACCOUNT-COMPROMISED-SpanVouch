from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from spanvouch.adapters.storage.sqlite_schema import connect_database, initialize_database
from spanvouch.projects.models import Project
from spanvouch.security.identity import (
    ApiKeyMaterial,
    ApiKeyRecord,
    AuthenticationError,
    Principal,
    Role,
)


class ProjectPersistenceError(RuntimeError):
    """Raised when project persistence cannot complete safely."""


class ProjectConflictError(ValueError):
    """Raised when project persistence constraints are violated."""


class ProjectNotFoundError(KeyError):
    """Raised when a requested project does not exist."""


class ApiKeyNotFoundError(KeyError):
    """Raised when a requested API key does not exist."""


class ApiKeyConflictError(ValueError):
    """Raised when an API key lifecycle operation cannot be applied."""


class ProjectRepository:
    def __init__(self, database: str | Path) -> None:
        value = os.fspath(database)
        if value == ":memory:" or value.startswith("file:"):
            raise ValueError(
                "project database must be a filesystem path; "
                "SQLite memory databases and file: URIs are unsupported"
            )
        self._database = Path(value)

    async def initialize(self) -> None:
        await asyncio.to_thread(initialize_database, self._database)

    def create_project(self, name: str, *, now: datetime) -> Project:
        if not name.strip():
            raise ValueError("project name is required")
        project = Project(
            project_id=uuid4().hex,
            name=name,
            created_at=now,
            updated_at=now,
        )
        with self._transaction(write=True) as connection:
            try:
                connection.execute(
                    "INSERT INTO projects(project_id, name, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        project.project_id,
                        project.name,
                        _timestamp(project.created_at),
                        _timestamp(project.updated_at),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ProjectConflictError("project already exists") from error
        return project

    def list_projects(self) -> tuple[Project, ...]:
        with self._transaction(write=False) as connection:
            rows = connection.execute(
                "SELECT project_id, name, created_at, updated_at FROM projects "
                "ORDER BY created_at, project_id"
            ).fetchall()
        return tuple(_project_from_row(row) for row in rows)

    def create_key(
        self,
        project_id: str | None,
        roles: tuple[Role, ...],
        *,
        now: datetime,
        expires_at: datetime | None,
    ) -> tuple[ApiKeyRecord, str]:
        record, plaintext = ApiKeyMaterial.create(
            project_id=project_id,
            roles=roles,
            now=now,
            expires_at=expires_at,
        )
        with self._transaction(write=True) as connection:
            if project_id is not None:
                _require_project(connection, project_id)
            _insert_key(connection, record)
        return record, plaintext

    def authenticate(self, presented: str, *, now: datetime) -> Principal:
        key_id = _presented_key_id(presented)
        with self._transaction(write=False) as connection:
            row = connection.execute(
                "SELECT * FROM api_keys WHERE key_id = ? "
                "AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)",
                (key_id, _timestamp(now)),
            ).fetchone()
        if row is None:
            raise AuthenticationError("api key is invalid")
        record = _key_record_from_row(row)
        if not ApiKeyMaterial.verify(presented, record, now=now):
            raise AuthenticationError("api key is invalid")
        return Principal(
            key_id=record.key_id,
            project_id=record.project_id,
            roles=record.roles,
        )

    def rotate_key(
        self,
        key_id: str,
        *,
        now: datetime,
    ) -> tuple[ApiKeyRecord, str]:
        with self._transaction(write=True) as connection:
            record = _require_key(connection, key_id)
            _require_active_key(record, now)
            new_record, plaintext = ApiKeyMaterial.create(
                project_id=record.project_id,
                roles=record.roles,
                now=now,
                expires_at=record.expires_at,
            )
            _insert_key(connection, new_record)
            cursor = connection.execute(
                "UPDATE api_keys SET revoked_at = ?, replaced_by_key_id = ? "
                "WHERE key_id = ? AND revoked_at IS NULL",
                (_timestamp(now), new_record.key_id, record.key_id),
            )
            if cursor.rowcount != 1:
                raise ApiKeyConflictError("api key rotation conflict")
        return new_record, plaintext

    def revoke_key(self, key_id: str, *, now: datetime) -> None:
        with self._transaction(write=True) as connection:
            record = _require_key(connection, key_id)
            if record.revoked_at is not None:
                return
            cursor = connection.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE key_id = ? AND revoked_at IS NULL",
                (_timestamp(now), key_id),
            )
            if cursor.rowcount != 1:
                raise ApiKeyConflictError("api key revocation conflict")

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        try:
            connection = connect_database(self._database)
        except sqlite3.Error as error:
            raise ProjectPersistenceError("project persistence operation failed") from error
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise ProjectConflictError("project persistence constraint conflict") from error
        except sqlite3.Error as error:
            connection.rollback()
            raise ProjectPersistenceError("project persistence operation failed") from error
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _project_from_row(row: sqlite3.Row) -> Project:
    return Project(
        project_id=str(row["project_id"]),
        name=str(row["name"]),
        created_at=_parse_timestamp(str(row["created_at"])),
        updated_at=_parse_timestamp(str(row["updated_at"])),
    )


def _insert_key(connection: sqlite3.Connection, record: ApiKeyRecord) -> None:
    connection.execute(
        "INSERT INTO api_keys("
        "key_id, prefix, project_id, roles_json, secret_salt, secret_digest, "
        "created_at, expires_at, revoked_at, replaced_by_key_id"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            record.key_id,
            record.prefix,
            record.project_id,
            _roles_json(record.roles),
            record.secret_salt,
            record.secret_digest,
            _timestamp(record.created_at),
            _timestamp_optional(record.expires_at),
            _timestamp_optional(record.revoked_at),
        ),
    )


def _require_project(connection: sqlite3.Connection, project_id: str) -> None:
    row = connection.execute(
        "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
    ).fetchone()
    if row is None:
        raise ProjectNotFoundError(project_id)


def _require_key(connection: sqlite3.Connection, key_id: str) -> ApiKeyRecord:
    row = connection.execute("SELECT * FROM api_keys WHERE key_id = ?", (key_id,)).fetchone()
    if row is None:
        raise ApiKeyNotFoundError(key_id)
    return _key_record_from_row(row)


def _require_active_key(record: ApiKeyRecord, now: datetime) -> None:
    if record.revoked_at is not None:
        raise ApiKeyConflictError("api key is inactive")
    if record.expires_at is not None and now >= record.expires_at:
        raise ApiKeyConflictError("api key is inactive")


def _key_record_from_row(row: sqlite3.Row) -> ApiKeyRecord:
    return ApiKeyRecord(
        key_id=str(row["key_id"]),
        prefix=str(row["prefix"]),
        project_id=str(row["project_id"]) if row["project_id"] is not None else None,
        roles=_roles_from_json(str(row["roles_json"])),
        secret_salt=bytes(row["secret_salt"]),
        secret_digest=bytes(row["secret_digest"]),
        created_at=_parse_timestamp(str(row["created_at"])),
        expires_at=_parse_timestamp_optional(row["expires_at"]),
        revoked_at=_parse_timestamp_optional(row["revoked_at"]),
    )


def _presented_key_id(value: str) -> str:
    try:
        prefix, key_id, secret = value.split("_", maxsplit=2)
    except ValueError:
        raise AuthenticationError("api key is malformed") from None
    if prefix != "svk" or not key_id or not secret:
        raise AuthenticationError("api key is malformed")
    return key_id


def _roles_json(roles: tuple[Role, ...]) -> str:
    return json.dumps([role.value for role in roles], separators=(",", ":"))


def _roles_from_json(value: str) -> tuple[Role, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("stored api key roles are invalid")
    return tuple(Role(item) for item in parsed)


def _timestamp(value: datetime) -> str:
    return value.isoformat()


def _timestamp_optional(value: datetime | None) -> str | None:
    return _timestamp(value) if value is not None else None


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError("stored timestamp must be aware UTC")
    return parsed


def _parse_timestamp_optional(value: object) -> datetime | None:
    return _parse_timestamp(str(value)) if value is not None else None
