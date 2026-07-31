from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from spanvouch.adapters.storage.sqlite_schema import initialize_database
from spanvouch.api.app import create_app
from spanvouch.projects.repository import ProjectRepository
from spanvouch.security.identity import Role
from spanvouch.trace.repository import InMemoryTraceRepository
from tests.trace.test_diagnostic_view import load_trace


def _operator_key(repository: ProjectRepository, project_name: str) -> str:
    now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    project = repository.create_project(project_name, now=now)
    _, plaintext = repository.create_key(
        project.project_id,
        (Role.OPERATOR,),
        now=now,
        expires_at=None,
    )
    return plaintext


def _authorization(plaintext: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {plaintext}"}


def test_trace_diagnosis_is_hidden_across_projects(tmp_path: Path) -> None:
    database = tmp_path / "spanvouch.db"
    initialize_database(database)
    project_repository = ProjectRepository(database)
    alpha_key = _operator_key(project_repository, "Alpha")
    beta_key = _operator_key(project_repository, "Beta")
    client = TestClient(
        create_app(
            trace_repository=InMemoryTraceRepository(),
            review_database=database,
            project_repository=project_repository,
        )
    )
    trace = load_trace("invalid_argument-01")

    created = client.post(
        "/v1/traces",
        json=trace.model_dump(mode="json"),
        headers=_authorization(alpha_key),
    )
    visible = client.post(
        f"/v1/traces/{trace.trace_id}/diagnoses",
        json={},
        headers=_authorization(alpha_key),
    )
    hidden = client.post(
        f"/v1/traces/{trace.trace_id}/diagnoses",
        json={},
        headers=_authorization(beta_key),
    )

    assert created.status_code == 201
    assert visible.status_code == 200
    assert hidden.status_code == 404
