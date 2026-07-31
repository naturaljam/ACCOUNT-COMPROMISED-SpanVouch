from __future__ import annotations

import sqlite3
from pathlib import Path

from spanvouch.adapters.storage.sqlite_schema import connect_database, initialize_database
from spanvouch.adapters.storage.sqlite_trace import SQLiteTraceRepository
from spanvouch.contracts.review import DecisionAction
from tests.api.helpers import make_admin_client, make_project_client
from tests.trace.test_diagnostic_view import load_trace


def _audit_events(database: Path) -> tuple[sqlite3.Row, ...]:
    with connect_database(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM audit_events ORDER BY project_id, event_sequence"
        ).fetchall()
    return tuple(rows)


def test_admin_project_and_key_management_are_audited(tmp_path: Path) -> None:
    database = tmp_path / "audit.db"
    initialize_database(database)
    context = make_admin_client(database=database)

    with context.client as client:
        created = client.post("/v1/admin/projects", json={"name": "Alpha"})
        assert created.status_code == 201
        project_id = created.json()["project_id"]

        key = client.post(
            f"/v1/admin/projects/{project_id}/api-keys",
            json={"roles": ["operator", "reviewer"]},
        )
        assert key.status_code == 201
        key_id = key.json()["key_id"]

        rotated = client.post(f"/v1/admin/api-keys/{key_id}/rotate")
        assert rotated.status_code == 200

        revoked = client.post(f"/v1/admin/api-keys/{rotated.json()['key_id']}/revoke")
        assert revoked.status_code == 204

    rows = _audit_events(database)
    assert [row["action"] for row in rows] == [
        "project.create",
        "api_key.create",
        "api_key.rotate",
        "api_key.revoke",
    ]
    assert [row["project_id"] for row in rows] == [project_id] * 4
    assert [row["event_sequence"] for row in rows] == [0, 1, 2, 3]


def test_trace_and_review_requests_emit_audit_rows(tmp_path: Path) -> None:
    database = tmp_path / "audit.db"
    initialize_database(database)
    admin_context = make_admin_client(database=database)
    with admin_context.client as client:
        created = client.post("/v1/admin/projects", json={"name": "Alpha"})
        assert created.status_code == 201

    project_context = make_project_client(
        database=database,
        project_name="Alpha",
        trace_repository=SQLiteTraceRepository(database),
    )
    with project_context.client as client:
        trace = load_trace("invalid_argument-01")
        created_trace = client.post("/v1/traces", json=trace.model_dump(mode="json"))
        assert created_trace.status_code == 201

        review = client.post(
            f"/v1/traces/{trace.trace_id}/diagnosis-reviews",
            json={"idempotency_key": "review-audit-1"},
        )
        assert review.status_code == 201
        case_id = review.json()["case"]["case_id"]
        decision = client.post(
            f"/v1/diagnosis-reviews/{case_id}/decisions",
            json={
                "action": DecisionAction.CONFIRM.value,
                "expected_version": review.json()["case"]["version"],
                "reviewer_label": "auditor",
                "reason": "audit coverage",
                "idempotency_key": "review-audit-2",
            },
        )
        assert decision.status_code == 200

    rows = _audit_events(database)
    actions = [row["action"] for row in rows]
    assert "trace.ingest" in actions
    assert "review.create" in actions
    assert "review.decide" in actions
    assert len(rows) >= 3
