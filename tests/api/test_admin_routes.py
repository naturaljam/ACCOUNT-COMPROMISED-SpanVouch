from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from spanvouch.adapters.storage.sqlite_schema import initialize_database
from spanvouch.api.app import create_app
from spanvouch.projects.repository import ProjectRepository
from spanvouch.security.identity import Role
from spanvouch.trace.repository import InMemoryTraceRepository


def _admin_client(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    database = tmp_path / "spanvouch.db"
    initialize_database(database)
    repository = ProjectRepository(database)
    _, plaintext = repository.create_key(
        None,
        (Role.ADMIN,),
        now=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
        expires_at=None,
    )
    client = TestClient(
        create_app(
            trace_repository=InMemoryTraceRepository(),
            review_database=database,
            project_repository=repository,
        )
    )
    return client, {"Authorization": f"Bearer {plaintext}"}


def _trace_payload(trace_id: str) -> dict[str, object]:
    now = datetime(2026, 7, 31, tzinfo=UTC).isoformat()
    return {
        "schema_name": "spanvouch.trace",
        "schema_version": "1.0",
        "trace_id": trace_id,
        "run_id": f"run-{trace_id}",
        "spans": [
            {
                "trace_id": trace_id,
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


def test_admin_can_create_and_list_projects(tmp_path: Path) -> None:
    client, headers = _admin_client(tmp_path)

    created = client.post(
        "/v1/admin/projects",
        json={"name": "Alpha"},
        headers=headers,
    )
    listed = client.get("/v1/admin/projects", headers=headers)

    assert created.status_code == 201
    created_body = created.json()
    assert created_body["name"] == "Alpha"
    assert created_body["project_id"]
    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()["projects"]] == [
        "Default project",
        "Alpha",
    ]


def test_admin_can_create_rotate_and_revoke_project_keys(tmp_path: Path) -> None:
    client, admin_headers = _admin_client(tmp_path)
    project_id = client.post(
        "/v1/admin/projects",
        json={"name": "Alpha"},
        headers=admin_headers,
    ).json()["project_id"]

    created = client.post(
        f"/v1/admin/projects/{project_id}/api-keys",
        json={"roles": ["operator"], "expires_at": None},
        headers=admin_headers,
    )

    assert created.status_code == 201
    created_body = created.json()
    assert created_body["project_id"] == project_id
    assert created_body["roles"] == ["operator"]
    assert created_body["api_key"].startswith(f"{created_body['prefix']}_")
    operator_headers = {"Authorization": f"Bearer {created_body['api_key']}"}
    assert (
        client.post(
            "/v1/traces",
            json=_trace_payload("trace-admin-key-old"),
            headers=operator_headers,
        ).status_code
        == 201
    )

    rotated = client.post(
        f"/v1/admin/api-keys/{created_body['key_id']}/rotate",
        headers=admin_headers,
    )

    assert rotated.status_code == 200
    rotated_body = rotated.json()
    assert rotated_body["key_id"] != created_body["key_id"]
    assert rotated_body["project_id"] == project_id
    assert rotated_body["roles"] == ["operator"]
    assert (
        client.post(
            "/v1/traces",
            json=_trace_payload("trace-admin-key-stale"),
            headers=operator_headers,
        ).status_code
        == 401
    )
    rotated_headers = {"Authorization": f"Bearer {rotated_body['api_key']}"}
    assert (
        client.post(
            "/v1/traces",
            json=_trace_payload("trace-admin-key-new"),
            headers=rotated_headers,
        ).status_code
        == 201
    )

    revoked = client.post(
        f"/v1/admin/api-keys/{rotated_body['key_id']}/revoke",
        headers=admin_headers,
    )

    assert revoked.status_code == 204
    assert (
        client.post(
            "/v1/traces",
            json=_trace_payload("trace-admin-key-revoked"),
            headers=rotated_headers,
        ).status_code
        == 401
    )
