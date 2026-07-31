from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from spanvouch.adapters.storage.sqlite_schema import connect_database, initialize_database
from spanvouch.audit.chain import AuditChain, AuditEvent, AuditEventInput
from spanvouch.audit.export import create_audit_export, verify_audit_export
from spanvouch.projects.repository import ProjectRepository
from spanvouch.security.identity import Role
from tests.api.helpers import make_admin_client

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def _write_signing_key(path: Path) -> Ed25519PrivateKey:
    key = Ed25519PrivateKey.generate()
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return key


def _bootstrap_audit_events(database: Path) -> tuple[str, tuple[AuditEvent, ...]]:
    initialize_database(database)
    repository = ProjectRepository(database)
    project = repository.create_project("Alpha", now=NOW)
    record, _ = repository.create_key(None, (Role.ADMIN,), now=NOW, expires_at=None)
    chain = AuditChain()
    with connect_database(database) as connection:
        first = chain.append(
            connection,
            AuditEventInput(
                project_id=project.project_id,
                actor_key_id=record.key_id,
                action="project.create",
                resource_type="project",
                resource_id=project.project_id,
                result="created",
                payload={"name": "Alpha"},
                occurred_at=NOW,
                request_id="req-1",
            ),
        )
        second = chain.append(
            connection,
            AuditEventInput(
                project_id=project.project_id,
                actor_key_id=record.key_id,
                action="trace.ingest",
                resource_type="trace",
                resource_id="trace-1",
                result="created",
                payload={"trace_id": "trace-1"},
                occurred_at=NOW,
                request_id="req-2",
            ),
        )
    return project.project_id, (first, second)


def test_create_audit_export_writes_offline_verifiable_bundle(tmp_path: Path) -> None:
    database = tmp_path / "audit.db"
    project_id, events = _bootstrap_audit_events(database)
    signing_key_path = tmp_path / "audit-signing-key.pem"
    _write_signing_key(signing_key_path)

    bundle = create_audit_export(
        project_id,
        tmp_path / "bundle",
        events=events,
        checkpoints=(),
        signing_key_path=signing_key_path,
    )

    assert {path.name for path in bundle.iterdir()} == {
        "README.md",
        "checkpoints.json",
        "events.jsonl",
        "manifest.json",
        "public-key.pem",
    }
    verified = verify_audit_export(bundle)
    assert verified.project_id == project_id
    assert verified.first_event_sequence == 0
    assert verified.last_event_sequence == 1
    assert verified.terminal_event_sha256 == events[-1].event_sha256
    assert verified.event_count == 2
    assert verified.checkpoint_count == 1


def test_admin_can_create_list_and_get_audit_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "audit.db"
    initialize_database(database)
    signing_key_path = tmp_path / "audit-signing-key.pem"
    _write_signing_key(signing_key_path)
    export_root = tmp_path / "exports"
    monkeypatch.setenv("SPANVOUCH_AUDIT_SIGNING_KEY_PATH", str(signing_key_path))
    monkeypatch.setenv("SPANVOUCH_AUDIT_EXPORT_DIR", str(export_root))
    context = make_admin_client(database=database)

    with context.client as client:
        created_project = client.post("/v1/admin/projects", json={"name": "Alpha"})
        project_id = created_project.json()["project_id"]

        created_export = client.post(f"/v1/admin/projects/{project_id}/audit-exports")
        assert created_export.status_code == 201
        body = created_export.json()
        assert body["project_id"] == project_id
        assert body["first_event_sequence"] == 0
        assert body["last_event_sequence"] == 0
        assert "private" not in json.dumps(body).lower()
        assert Path(body["bundle_path"]).is_dir()

        listed = client.get("/v1/admin/audit-exports")
        assert listed.status_code == 200
        assert listed.json()["exports"] == [body]

        fetched = client.get(f"/v1/admin/audit-exports/{body['export_id']}")
        assert fetched.status_code == 200
        assert fetched.json() == body
