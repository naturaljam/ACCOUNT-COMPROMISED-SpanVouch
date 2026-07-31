from __future__ import annotations

import sqlite3
from pathlib import Path

from spanvouch.adapters.storage import sqlite_schema
from spanvouch.adapters.storage.sqlite_schema import connect_database, initialize_database

REQUIRED_INDEXES = {
    "idx_review_cases_project_id",
    "idx_review_inputs_project_id",
    "idx_diagnosis_revisions_project_id",
    "idx_verifier_runs_project_id",
    "idx_human_decisions_project_id",
    "idx_workflow_events_project_id",
    "idx_idempotency_keys_project_id",
    "idx_traces_project_id",
    "idx_api_keys_project_id",
    "idx_audit_events_project_id",
    "idx_audit_checkpoints_project_id",
    "idx_audit_exports_project_id",
}


def _create_v3_database(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        for statement in sqlite_schema._SCHEMA_SQL.split(";"):
            if statement.strip():
                connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_metadata(singleton_key, schema_version) VALUES (1, 3)"
        )
        connection.execute(
            "INSERT INTO traces(trace_id, run_id, trace_json, trace_sha256) "
            "VALUES ('trace-before-v4', 'run-before-v4', '{}', ?) ",
            ("a" * 64,),
        )
        connection.execute(
            "INSERT INTO review_cases("
            "case_id, status, version, verification_mode, diagnoser, "
            "current_revision_number, evidence_revision_count, created_at, updated_at"
            ") VALUES ('case-before-v4', 'pending_verification', 0, 'deterministic', "
            "'rules', 0, 0, '2026-07-31T00:00:00Z', '2026-07-31T00:00:00Z')"
        )


def _index_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'index' AND name NOT LIKE 'sqlite_autoindex_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def test_initialize_migrates_v3_rows_into_default_project_without_data_loss(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-v3.sqlite3"
    _create_v3_database(database)

    initialize_database(database)

    with connect_database(database) as connection:
        assert connection.execute(
            "SELECT schema_version FROM schema_metadata WHERE singleton_key = 1"
        ).fetchone() == (4,)
        assert _index_names(connection) == REQUIRED_INDEXES
        assert connection.execute(
            "SELECT project_id, name FROM projects WHERE project_id = 'default'"
        ).fetchone() == ("default", "Default project")
        assert connection.execute(
            "SELECT trace_id, project_id FROM traces WHERE trace_id = 'trace-before-v4'"
        ).fetchone() == ("trace-before-v4", "default")
        assert connection.execute(
            "SELECT case_id, project_id FROM review_cases WHERE case_id = 'case-before-v4'"
        ).fetchone() == ("case-before-v4", "default")


def test_initialize_creates_v4_audit_and_key_tables(tmp_path: Path) -> None:
    database = tmp_path / "fresh-v4.sqlite3"

    initialize_database(database)

    with connect_database(database) as connection:
        names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indexes = _index_names(connection)
    assert {
        "projects",
        "api_keys",
        "audit_events",
        "audit_checkpoints",
        "audit_exports",
    } <= names
    assert indexes == REQUIRED_INDEXES
