import sqlite3
from pathlib import Path

from spanvouch.review.errors import ReviewSchemaError

SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 5_000

_SCHEMA_SQL = """
CREATE TABLE schema_metadata (
    singleton_key INTEGER PRIMARY KEY CHECK (singleton_key = 1),
    schema_version INTEGER NOT NULL
);

CREATE TABLE review_cases (
    case_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 0),
    verification_mode TEXT NOT NULL,
    diagnoser TEXT NOT NULL,
    current_revision_number INTEGER NOT NULL CHECK (current_revision_number >= 0),
    evidence_revision_count INTEGER NOT NULL
        CHECK (evidence_revision_count BETWEEN 0 AND 1),
    deterministic_run_id TEXT,
    semantic_run_id TEXT,
    composite_verdict TEXT,
    terminal_decision_id TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (case_id, deterministic_run_id)
        REFERENCES verifier_runs(case_id, verifier_run_id),
    FOREIGN KEY (case_id, semantic_run_id)
        REFERENCES verifier_runs(case_id, verifier_run_id),
    FOREIGN KEY (case_id, terminal_decision_id)
        REFERENCES human_decisions(case_id, decision_id)
);

CREATE TABLE review_inputs (
    case_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    view_json TEXT NOT NULL,
    input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
    catalog_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES review_cases(case_id)
);

CREATE TABLE diagnosis_revisions (
    revision_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 0),
    origin TEXT NOT NULL,
    previous_report_sha256 TEXT
        CHECK (previous_report_sha256 IS NULL OR length(previous_report_sha256) = 64),
    report_json TEXT NOT NULL,
    report_sha256 TEXT NOT NULL CHECK (length(report_sha256) = 64),
    triggering_gap_ids_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (case_id, revision_number),
    FOREIGN KEY (case_id) REFERENCES review_cases(case_id)
);

CREATE TABLE verifier_runs (
    verifier_run_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 0),
    verifier_kind TEXT NOT NULL,
    report_json TEXT NOT NULL,
    verdict TEXT NOT NULL,
    usage_json TEXT,
    operational_error_json TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (case_id, verifier_run_id),
    FOREIGN KEY (case_id) REFERENCES review_cases(case_id),
    FOREIGN KEY (case_id, revision_number)
        REFERENCES diagnosis_revisions(case_id, revision_number)
);

CREATE TABLE human_decisions (
    decision_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL,
    reviewer_label TEXT NOT NULL,
    reason TEXT,
    expected_version INTEGER NOT NULL CHECK (expected_version >= 0),
    correction_revision_number INTEGER CHECK (correction_revision_number >= 0),
    created_at TEXT NOT NULL,
    UNIQUE (case_id, decision_id),
    FOREIGN KEY (case_id) REFERENCES review_cases(case_id),
    FOREIGN KEY (case_id, correction_revision_number)
        REFERENCES diagnosis_revisions(case_id, revision_number)
);

CREATE TABLE workflow_events (
    event_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    event_sequence INTEGER NOT NULL CHECK (event_sequence >= 0),
    event_type TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    case_version INTEGER NOT NULL CHECK (case_version >= 0),
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (case_id, event_sequence),
    FOREIGN KEY (case_id) REFERENCES review_cases(case_id)
);

CREATE TABLE idempotency_keys (
    scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64),
    result_type TEXT NOT NULL,
    result_id TEXT,
    reservation_id TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (result_id IS NOT NULL AND reservation_id IS NULL AND lease_expires_at IS NULL)
        OR
        (result_id IS NULL AND result_type = 'review_case'
            AND reservation_id IS NOT NULL AND lease_expires_at IS NOT NULL)
    ),
    PRIMARY KEY (scope, idempotency_key)
);
"""


def _normalize_schema_sql(value: str) -> str:
    return " ".join(value.rstrip(";").split())


_EXPECTED_SCHEMA_SQL = {
    statement.strip().split()[2]: _normalize_schema_sql(statement.strip())
    for statement in _SCHEMA_SQL.split(";")
    if statement.strip()
}


def _validate_schema_v2(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    actual = {
        str(name): _normalize_schema_sql(str(sql))
        for name, sql in rows
        if sql is not None
    }
    if actual != _EXPECTED_SCHEMA_SQL:
        raise ReviewSchemaError("review schema structure does not match version 2")


def connect_database(database: str | Path) -> sqlite3.Connection:
    """Open one configured connection; callers own its transaction and lifetime."""
    connection = sqlite3.connect(database, timeout=BUSY_TIMEOUT_MS / 1_000)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        return connection
    except Exception:
        connection.close()
        raise


def initialize_database(database: str | Path) -> None:
    """Create schema v2 or verify that an existing database is exactly v2."""
    connection = connect_database(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        has_metadata = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_metadata'"
        ).fetchone()
        if has_metadata:
            row = connection.execute(
                "SELECT schema_version FROM schema_metadata WHERE singleton_key = 1"
            ).fetchone()
            if row != (SCHEMA_VERSION,):
                raise ReviewSchemaError("unsupported review schema version")
            _validate_schema_v2(connection)
        else:
            for statement in _SCHEMA_SQL.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_metadata(singleton_key, schema_version) VALUES (1, ?)",
                (SCHEMA_VERSION,),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
