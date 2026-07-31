from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Barrier

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from spanvouch.adapters.storage.sqlite_schema import connect_database, initialize_database
from spanvouch.audit.chain import AuditChain, AuditEventInput
from spanvouch.contracts.versioning import canonical_json
from spanvouch.projects.repository import ProjectRepository
from spanvouch.security.identity import Role
from tests.api.helpers import NOW


def _expected_sha256(payload: dict[str, object]) -> str:
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _bootstrap_project(database: Path) -> tuple[str, str]:
    initialize_database(database)
    repository = ProjectRepository(database)
    project = repository.create_project("Alpha", now=NOW)
    record, _ = repository.create_key(None, (Role.ADMIN,), now=NOW, expires_at=None)
    return project.project_id, record.key_id


def test_append_assigns_project_local_sequence_and_hash(tmp_path: Path) -> None:
    database = tmp_path / "audit.db"
    project_id, actor_key_id = _bootstrap_project(database)
    chain = AuditChain()

    event_input = AuditEventInput(
        project_id=project_id,
        actor_key_id=actor_key_id,
        action="project.create",
        resource_type="project",
        resource_id=project_id,
        result="created",
        payload={"name": "Alpha"},
        occurred_at=NOW,
        request_id="req-1",
    )

    with connect_database(database) as connection:
        appended = chain.append(connection, event_input)

    assert appended.project_id == project_id
    assert appended.event_sequence == 0
    assert appended.previous_event_sha256 is None
    assert appended.actor_key_id == actor_key_id
    assert appended.actor_roles == (Role.ADMIN,)
    assert appended.payload == {"name": "Alpha"}
    assert appended.event_sha256 == _expected_sha256(
        {
            "project_id": project_id,
            "event_sequence": 0,
            "previous_event_sha256": None,
            "actor_key_id": actor_key_id,
            "actor_roles": ["admin"],
            "action": "project.create",
            "resource_type": "project",
            "resource_id": project_id,
            "result": "created",
            "payload": {"name": "Alpha"},
            "request_id": "req-1",
            "occurred_at": NOW,
        }
    )


def test_verify_rejects_tampered_chain(tmp_path: Path) -> None:
    database = tmp_path / "audit.db"
    project_id, actor_key_id = _bootstrap_project(database)
    chain = AuditChain()

    with connect_database(database) as connection:
        first = chain.append(
            connection,
            AuditEventInput(
                project_id=project_id,
                actor_key_id=actor_key_id,
                action="trace.ingest",
                resource_type="trace",
                resource_id="trace-1",
                result="created",
                payload={"trace_id": "trace-1"},
                occurred_at=NOW,
                request_id="req-1",
            ),
        )
        second = chain.append(
            connection,
            AuditEventInput(
                project_id=project_id,
                actor_key_id=actor_key_id,
                action="diagnosis.request",
                resource_type="trace",
                resource_id="trace-1",
                result="created",
                payload={"diagnoser": "rules"},
                occurred_at=NOW,
                request_id="req-1",
            ),
        )

    chain.verify((first, second))

    with pytest.raises(ValueError):
        chain.verify((first, second.model_copy(update={"payload": {"tampered": True}})))


def test_append_serializes_concurrent_writers(tmp_path: Path) -> None:
    database = tmp_path / "audit.db"
    project_id, actor_key_id = _bootstrap_project(database)
    chain = AuditChain()
    barrier = Barrier(2)

    def append(index: int) -> int:
        with connect_database(database) as connection:
            barrier.wait(timeout=10)
            appended = chain.append(
                connection,
                AuditEventInput(
                    project_id=project_id,
                    actor_key_id=actor_key_id,
                    action=f"trace.ingest.{index}",
                    resource_type="trace",
                    resource_id=f"trace-{index}",
                    result="created",
                    payload={"trace_id": f"trace-{index}"},
                    occurred_at=NOW,
                    request_id=f"req-{index}",
                ),
            )
            return appended.event_sequence

    with ThreadPoolExecutor(max_workers=2) as pool:
        sequences = sorted(pool.map(append, (1, 2)))

    assert sequences == [0, 1]


def test_create_checkpoint_signs_terminal_event(tmp_path: Path) -> None:
    database = tmp_path / "audit.db"
    project_id, actor_key_id = _bootstrap_project(database)
    chain = AuditChain(signing_key=Ed25519PrivateKey.generate())

    with connect_database(database) as connection:
        event = chain.append(
            connection,
            AuditEventInput(
                project_id=project_id,
                actor_key_id=actor_key_id,
                action="review.create",
                resource_type="review_case",
                resource_id="case-1",
                result="created",
                payload={"case_id": "case-1"},
                occurred_at=NOW,
                request_id="req-1",
            ),
        )

    checkpoint = chain.create_checkpoint(
        project_id=project_id,
        terminal_event=event,
        manifest_sha256="a" * 64,
    )

    public_key = serialization.load_pem_public_key(checkpoint.public_key_pem)
    public_key.verify(
        checkpoint.signature,
        canonical_json(
            {
                "project_id": project_id,
                "first_event_sequence": 0,
                "last_event_sequence": event.event_sequence,
                "terminal_event_sha256": event.event_sha256,
                "manifest_sha256": "a" * 64,
            }
        ).encode("utf-8"),
    )
    assert checkpoint.project_id == project_id
    assert checkpoint.first_event_sequence == 0
    assert checkpoint.last_event_sequence == event.event_sequence
    assert checkpoint.terminal_event_sha256 == event.event_sha256
