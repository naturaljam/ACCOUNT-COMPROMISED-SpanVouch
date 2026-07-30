import sqlite3
from pathlib import Path

import pytest

from spanvouch.adapters.storage.sqlite_schema import (
    SCHEMA_VERSION,
    connect_database,
    initialize_database,
)
from spanvouch.review.errors import ReviewSchemaError

REQUIRED_TABLES = {
    "schema_metadata",
    "review_cases",
    "review_inputs",
    "diagnosis_revisions",
    "verifier_runs",
    "human_decisions",
    "workflow_events",
    "idempotency_keys",
    "traces",
}


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def test_initialize_creates_exact_schema_v3_and_is_repeatable(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite3"

    initialize_database(database)
    initialize_database(database)

    with connect_database(database) as connection:
        assert _table_names(connection) == REQUIRED_TABLES
        assert connection.execute(
            "SELECT schema_version FROM schema_metadata WHERE singleton_key = 1"
        ).fetchone() == (3,)
    assert SCHEMA_VERSION == 3


def test_connections_apply_required_pragmas(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite3"
    initialize_database(database)

    with connect_database(database) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] > 0


@pytest.mark.parametrize("schema_version", [0, 1, 99])
def test_initialize_refuses_unknown_schema_versions(
    tmp_path: Path, schema_version: int
) -> None:
    database = tmp_path / "reviews.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_metadata ("
            "singleton_key INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_metadata(singleton_key, schema_version) VALUES (1, ?)",
            (schema_version,),
        )

    with pytest.raises(ReviewSchemaError, match="unsupported review schema version"):
        initialize_database(database)


def test_initialize_refuses_incomplete_database_labeled_schema_v2(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_metadata ("
            "singleton_key INTEGER PRIMARY KEY CHECK (singleton_key = 1), "
            "schema_version INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_metadata(singleton_key, schema_version) VALUES (1, 2)"
        )

    with pytest.raises(ReviewSchemaError, match="schema structure"):
        initialize_database(database)


def test_schema_constraints_reject_invalid_audit_rows(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite3"
    initialize_database(database)

    with connect_database(database) as connection:
        columns = {
            table: tuple(
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            )
            for table in REQUIRED_TABLES
        }
        assert columns == {
            "schema_metadata": ("singleton_key", "schema_version"),
            "review_cases": (
                "case_id",
                "status",
                "version",
                "verification_mode",
                "diagnoser",
                "current_revision_number",
                "evidence_revision_count",
                "deterministic_run_id",
                "semantic_run_id",
                "composite_verdict",
                "terminal_decision_id",
                "lease_owner",
                "lease_expires_at",
                "created_at",
                "updated_at",
            ),
            "review_inputs": (
                "case_id",
                "trace_id",
                "run_id",
                "view_json",
                "input_sha256",
                "catalog_version",
                "created_at",
            ),
            "diagnosis_revisions": (
                "revision_id",
                "case_id",
                "revision_number",
                "origin",
                "previous_report_sha256",
                "report_json",
                "report_sha256",
                "triggering_gap_ids_json",
                "provenance_json",
                "created_at",
            ),
            "verifier_runs": (
                "verifier_run_id",
                "case_id",
                "revision_number",
                "verifier_kind",
                "report_json",
                "verdict",
                "usage_json",
                "operational_error_json",
                "started_at",
                "completed_at",
            ),
            "human_decisions": (
                "decision_id",
                "case_id",
                "action",
                "reviewer_label",
                "reason",
                "expected_version",
                "correction_revision_number",
                "created_at",
            ),
            "workflow_events": (
                "event_id",
                "case_id",
                "event_sequence",
                "event_type",
                "from_status",
                "to_status",
                "case_version",
                "metadata_json",
                "created_at",
            ),
            "idempotency_keys": (
                "scope",
                "idempotency_key",
                "request_sha256",
                "result_type",
                "result_id",
                "reservation_id",
                "lease_expires_at",
                "created_at",
                "updated_at",
            ),
            "traces": ("trace_id", "run_id", "trace_json", "trace_sha256"),
        }

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO review_cases("
                "case_id, status, version, verification_mode, diagnoser, "
                "current_revision_number, evidence_revision_count, created_at, updated_at"
                ") VALUES ('case-1', 'pending_verification', 0, 'deterministic', "
                "'rules', 0, 2, '2026-07-17T00:00:00Z', '2026-07-17T00:00:00Z')"
            )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO idempotency_keys("
                "scope, idempotency_key, request_sha256, result_type, result_id, "
                "created_at, updated_at"
                ") VALUES ('create', 'key', 'short', 'case', 'case-1', "
                "'2026-07-17T00:00:00Z', '2026-07-17T00:00:00Z')"
            )


def test_schema_enforces_required_uniqueness_and_sha256_lengths(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite3"
    initialize_database(database)
    sha256 = "a" * 64

    with connect_database(database) as connection:
        connection.execute(
            "INSERT INTO review_cases("
            "case_id, status, version, verification_mode, diagnoser, "
            "current_revision_number, evidence_revision_count, created_at, updated_at"
            ") VALUES ('case-1', 'pending_verification', 0, 'deterministic', "
            "'rules', 0, 0, '2026-07-17T00:00:00Z', '2026-07-17T00:00:00Z')"
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO review_inputs("
                "case_id, trace_id, run_id, view_json, input_sha256, catalog_version, created_at"
                ") VALUES ('case-1', 'trace-1', 'run-1', '{}', 'short', 'v1', "
                "'2026-07-17T00:00:00Z')"
            )
        connection.execute(
            "INSERT INTO review_inputs("
            "case_id, trace_id, run_id, view_json, input_sha256, catalog_version, created_at"
            ") VALUES ('case-1', 'trace-1', 'run-1', '{}', ?, 'v1', "
            "'2026-07-17T00:00:00Z')",
            (sha256,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO review_inputs("
                "case_id, trace_id, run_id, view_json, input_sha256, catalog_version, created_at"
                ") VALUES ('case-1', 'trace-2', 'run-2', '{}', ?, 'v1', "
                "'2026-07-17T00:00:00Z')",
                (sha256,),
            )

        revision_sql = (
            "INSERT INTO diagnosis_revisions("
            "revision_id, case_id, revision_number, origin, previous_report_sha256, "
            "report_json, report_sha256, triggering_gap_ids_json, provenance_json, created_at"
            ") VALUES (?, 'case-1', ?, 'initial_diagnosis', ?, '{}', ?, '[]', '{}', "
            "'2026-07-17T00:00:00Z')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(revision_sql, ("revision-bad-previous", 0, "short", sha256))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(revision_sql, ("revision-bad-report", 0, None, "short"))
        connection.execute(revision_sql, ("revision-0", 0, None, sha256))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(revision_sql, ("revision-duplicate", 0, None, sha256))

        verifier_sql = (
            "INSERT INTO verifier_runs("
            "verifier_run_id, case_id, revision_number, verifier_kind, report_json, verdict, "
            "started_at, completed_at) VALUES (?, 'case-1', 0, 'deterministic', '{}', "
            "'verified', '2026-07-17T00:00:00Z', '2026-07-17T00:00:01Z')"
        )
        connection.execute(verifier_sql, ("verifier-1",))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(verifier_sql, ("verifier-1",))

        decision_sql = (
            "INSERT INTO human_decisions("
            "decision_id, case_id, action, reviewer_label, expected_version, created_at"
            ") VALUES (?, 'case-1', 'confirm', 'reviewer', 0, '2026-07-17T00:00:00Z')"
        )
        connection.execute(decision_sql, ("decision-1",))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(decision_sql, ("decision-2",))

        event_sql = (
            "INSERT INTO workflow_events("
            "event_id, case_id, event_sequence, event_type, to_status, case_version, "
            "metadata_json, created_at) VALUES (?, 'case-1', 0, 'case_created', "
            "'pending_verification', 0, '{}', '2026-07-17T00:00:00Z')"
        )
        connection.execute(event_sql, ("event-1",))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(event_sql, ("event-2",))

        idempotency_sql = (
            "INSERT INTO idempotency_keys("
            "scope, idempotency_key, request_sha256, result_type, result_id, "
            "created_at, updated_at"
            ") VALUES ('scope', 'key', ?, 'review_case', 'case-1', "
            "'2026-07-17T00:00:00Z', '2026-07-17T00:00:00Z')"
        )
        connection.execute(idempotency_sql, (sha256,))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(idempotency_sql, (sha256,))


def test_initialize_migrates_v2_without_losing_review_state(tmp_path: Path) -> None:
    database = tmp_path / "reviews.sqlite3"
    initialize_database(database)
    with connect_database(database) as connection:
        connection.execute("DROP TABLE IF EXISTS traces")
        connection.execute(
            "UPDATE schema_metadata SET schema_version = 2 WHERE singleton_key = 1"
        )
        connection.execute(
            "INSERT INTO review_cases("
            "case_id, status, version, verification_mode, diagnoser, "
            "current_revision_number, evidence_revision_count, created_at, updated_at"
            ") VALUES ('case-before-migration', 'pending_verification', 0, "
            "'deterministic', 'rules', 0, 0, '2026-07-30T00:00:00Z', "
            "'2026-07-30T00:00:00Z')"
        )

    initialize_database(database)

    with connect_database(database) as connection:
        assert connection.execute(
            "SELECT schema_version FROM schema_metadata WHERE singleton_key = 1"
        ).fetchone() == (3,)
        assert connection.execute(
            "SELECT case_id FROM review_cases WHERE case_id = 'case-before-migration'"
        ).fetchone() == ("case-before-migration",)
        assert "traces" in _table_names(connection)
