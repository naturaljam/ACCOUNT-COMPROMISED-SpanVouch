from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spanvouch.adapters.storage.sqlite_schema import initialize_database
from spanvouch.api.app import create_app
from spanvouch.projects.repository import ProjectRepository
from spanvouch.security.identity import Role
from spanvouch.trace.repository import InMemoryTraceRepository


def _trace_payload() -> dict[str, object]:
    now = datetime(2026, 7, 31, tzinfo=UTC).isoformat()
    return {
        "schema_name": "spanvouch.trace",
        "schema_version": "1.0",
        "trace_id": "trace-auth-1",
        "run_id": "run-auth-1",
        "spans": [
            {
                "trace_id": "trace-auth-1",
                "span_id": "root",
                "parent_span_id": None,
                "name": "supportlab.run",
                "kind": "agent",
                "status": "ok",
                "started_at": now,
                "ended_at": now,
                "attributes": {},
            }
        ],
    }


def _project_repository(tmp_path: Path) -> tuple[ProjectRepository, str]:
    database = tmp_path / "spanvouch.db"
    initialize_database(database)
    repository = ProjectRepository(database)
    now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    project = repository.create_project("Alpha", now=now)
    return repository, project.project_id


def _authorization(plaintext: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {plaintext}"}


def test_health_and_ready_remain_public() -> None:
    client = TestClient(create_app(trace_repository=InMemoryTraceRepository()))

    assert client.get("/health").status_code == 200
    ready = client.get("/ready")

    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "service": "spanvouch"}


def test_protected_routes_reject_anonymous_requests() -> None:
    client = TestClient(create_app(trace_repository=InMemoryTraceRepository()))

    response = client.post("/v1/traces", json=_trace_payload())

    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "authentication_required"}}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/v1/traces/missing/diagnoses", {}),
        ("post", "/v1/traces/missing/diagnosis-reviews", {"idempotency_key": "create"}),
        ("get", "/v1/diagnosis-reviews/missing", None),
        ("post", "/v1/diagnosis-reviews/missing/resume", {}),
        (
            "post",
            "/v1/diagnosis-reviews/missing/decisions",
            {
                "action": "confirm",
                "expected_version": 0,
                "reviewer_label": "human",
                "idempotency_key": "decide",
            },
        ),
    ],
)
def test_all_non_probe_routes_reject_anonymous_requests(
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    client = TestClient(create_app(trace_repository=InMemoryTraceRepository()))

    response = client.request(method, path, json=body)

    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "authentication_required"}}


def test_protected_routes_accept_valid_project_key(tmp_path: Path) -> None:
    repository, project_id = _project_repository(tmp_path)
    _, plaintext = repository.create_key(
        project_id,
        (Role.OPERATOR,),
        now=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
        expires_at=None,
    )
    client = TestClient(
        create_app(
            trace_repository=InMemoryTraceRepository(),
            review_database=tmp_path / "spanvouch.db",
            project_repository=repository,
        )
    )

    response = client.post(
        "/v1/traces",
        json=_trace_payload(),
        headers=_authorization(plaintext),
    )

    assert response.status_code == 201


def test_protected_routes_reject_invalid_and_revoked_keys(tmp_path: Path) -> None:
    repository, project_id = _project_repository(tmp_path)
    now = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    record, plaintext = repository.create_key(
        project_id,
        (Role.OPERATOR,),
        now=now,
        expires_at=None,
    )
    repository.revoke_key(record.key_id, now=now)
    client = TestClient(
        create_app(
            trace_repository=InMemoryTraceRepository(),
            review_database=tmp_path / "spanvouch.db",
            project_repository=repository,
        )
    )

    invalid = client.post(
        "/v1/traces",
        json=_trace_payload(),
        headers=_authorization("svk_missing_secret"),
    )
    revoked = client.post(
        "/v1/traces",
        json=_trace_payload(),
        headers=_authorization(plaintext),
    )

    assert invalid.status_code == 401
    assert invalid.json() == {"detail": {"code": "authentication_failed"}}
    assert revoked.status_code == 401
    assert revoked.json() == {"detail": {"code": "authentication_failed"}}


def test_role_without_capability_returns_403(tmp_path: Path) -> None:
    repository, project_id = _project_repository(tmp_path)
    _, plaintext = repository.create_key(
        project_id,
        (Role.VIEWER,),
        now=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
        expires_at=None,
    )
    client = TestClient(
        create_app(
            trace_repository=InMemoryTraceRepository(),
            review_database=tmp_path / "spanvouch.db",
            project_repository=repository,
        )
    )

    response = client.post(
        "/v1/traces",
        json=_trace_payload(),
        headers=_authorization(plaintext),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "authorization_failed"}}
