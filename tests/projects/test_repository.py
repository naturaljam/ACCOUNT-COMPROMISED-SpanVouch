from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from spanvouch.adapters.storage.sqlite_schema import initialize_database
from spanvouch.projects.repository import ProjectConflictError, ProjectRepository


def test_create_project_assigns_generated_id_and_lists_projects(
    tmp_path: Path,
) -> None:
    database = tmp_path / "projects.sqlite3"
    initialize_database(database)
    repository = ProjectRepository(database)

    now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    project = repository.create_project("Alpha", now=now)

    assert project.name == "Alpha"
    assert project.project_id
    assert project.created_at == now
    assert project.updated_at == now

    projects = repository.list_projects()
    assert [item.project_id for item in projects] == ["default", project.project_id]
    assert [item.name for item in projects] == ["Default project", "Alpha"]


def test_create_project_rejects_duplicate_names(tmp_path: Path) -> None:
    database = tmp_path / "projects.sqlite3"
    initialize_database(database)
    repository = ProjectRepository(database)
    now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    repository.create_project("Alpha", now=now)

    with pytest.raises(ProjectConflictError):
        repository.create_project("Alpha", now=now + timedelta(minutes=1))
