from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from spanvouch.adapters.storage.sqlite_schema import initialize_database
from spanvouch.projects.repository import ProjectNotFoundError, ProjectRepository
from spanvouch.security.identity import AuthenticationError, Role


def test_create_key_stores_only_digest_and_authenticates(
    tmp_path: Path,
) -> None:
    database = tmp_path / "projects.sqlite3"
    initialize_database(database)
    repository = ProjectRepository(database)
    now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    project = repository.create_project("Alpha", now=now)

    record, plaintext = repository.create_key(
        project.project_id,
        (Role.OPERATOR,),
        now=now,
        expires_at=None,
    )

    assert plaintext.startswith(f"{record.prefix}_")
    assert plaintext != record.prefix
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM api_keys WHERE key_id = ?", (record.key_id,)
        ).fetchone()
    assert row is not None
    assert row["prefix"] == record.prefix
    assert row["project_id"] == project.project_id
    assert row["secret_salt"] == record.secret_salt
    assert row["secret_digest"] == record.secret_digest
    assert plaintext not in {str(value) for value in row}

    principal = repository.authenticate(plaintext, now=now)

    assert principal.key_id == record.key_id
    assert principal.project_id == project.project_id
    assert principal.roles == (Role.OPERATOR,)


def test_rotate_and_revoke_key_invalidates_presented_material(
    tmp_path: Path,
) -> None:
    database = tmp_path / "projects.sqlite3"
    initialize_database(database)
    repository = ProjectRepository(database)
    now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    project = repository.create_project("Alpha", now=now)
    record, old_plaintext = repository.create_key(
        project.project_id,
        (Role.VIEWER,),
        now=now,
        expires_at=None,
    )

    rotated_at = now + timedelta(minutes=5)
    new_record, new_plaintext = repository.rotate_key(record.key_id, now=rotated_at)

    assert new_record.key_id != record.key_id
    assert new_record.project_id == project.project_id
    assert new_record.roles == (Role.VIEWER,)
    assert new_plaintext != old_plaintext
    with pytest.raises(AuthenticationError):
        repository.authenticate(old_plaintext, now=rotated_at)
    assert repository.authenticate(new_plaintext, now=rotated_at).key_id == (
        new_record.key_id
    )
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        old_row = connection.execute(
            "SELECT revoked_at, replaced_by_key_id FROM api_keys WHERE key_id = ?",
            (record.key_id,),
        ).fetchone()
    assert old_row["revoked_at"] == rotated_at.isoformat()
    assert old_row["replaced_by_key_id"] == new_record.key_id

    revoked_at = rotated_at + timedelta(minutes=5)
    repository.revoke_key(new_record.key_id, now=revoked_at)

    with pytest.raises(AuthenticationError):
        repository.authenticate(new_plaintext, now=revoked_at)


def test_authenticate_rejects_expired_keys(tmp_path: Path) -> None:
    database = tmp_path / "projects.sqlite3"
    initialize_database(database)
    repository = ProjectRepository(database)
    now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    project = repository.create_project("Alpha", now=now)
    expires_at = now + timedelta(minutes=5)
    _, plaintext = repository.create_key(
        project.project_id,
        (Role.REVIEWER,),
        now=now,
        expires_at=expires_at,
    )

    assert repository.authenticate(plaintext, now=expires_at - timedelta(seconds=1))

    with pytest.raises(AuthenticationError):
        repository.authenticate(plaintext, now=expires_at)


def test_system_admin_key_authenticates_without_project_scope(tmp_path: Path) -> None:
    database = tmp_path / "projects.sqlite3"
    initialize_database(database)
    repository = ProjectRepository(database)
    now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)

    record, plaintext = repository.create_key(
        None,
        (Role.ADMIN,),
        now=now,
        expires_at=None,
    )

    principal = repository.authenticate(plaintext, now=now)

    assert principal.key_id == record.key_id
    assert principal.project_id is None
    assert principal.is_system_admin


def test_project_scoped_key_requires_existing_project(tmp_path: Path) -> None:
    database = tmp_path / "projects.sqlite3"
    initialize_database(database)
    repository = ProjectRepository(database)
    now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)

    with pytest.raises(ProjectNotFoundError):
        repository.create_key(
            "missing-project",
            (Role.VIEWER,),
            now=now,
            expires_at=None,
        )

    with sqlite3.connect(database) as connection:
        key_count = connection.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0]
    assert key_count == 0
