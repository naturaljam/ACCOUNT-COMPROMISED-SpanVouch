import sqlite3
from pathlib import Path

from spanvouch.review.errors import ReviewSchemaError

SCHEMA_VERSION = 4
PREVIOUS_SCHEMA_VERSION = 3
LEGACY_SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 5_000
_DEFAULT_PROJECT_ID = "default"
_DEFAULT_PROJECT_NAME = "Default project"
_DEFAULT_PROJECT_TIMESTAMP = "1970-01-01T00:00:00Z"

_SCHEMA_V2_SQL = """
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

_TRACE_SCHEMA_SQL = """
CREATE TABLE traces (
    trace_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    trace_json TEXT NOT NULL,
    trace_sha256 TEXT NOT NULL CHECK (length(trace_sha256) = 64)
);
"""

_SCHEMA_V3_SQL = _SCHEMA_V2_SQL + _TRACE_SCHEMA_SQL
# Retained for v3 fixture construction and legacy migration tests.
_SCHEMA_SQL = _SCHEMA_V3_SQL

_V4_TABLES_SQL = """
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE api_keys (
    key_id TEXT PRIMARY KEY,
    prefix TEXT NOT NULL UNIQUE,
    project_id TEXT,
    roles_json TEXT NOT NULL,
    secret_salt BLOB NOT NULL CHECK (length(secret_salt) = 16),
    secret_digest BLOB NOT NULL CHECK (length(secret_digest) = 32),
    created_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    replaced_by_key_id TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (replaced_by_key_id) REFERENCES api_keys(key_id)
);

CREATE TABLE audit_events (
    event_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    event_sequence INTEGER NOT NULL CHECK (event_sequence >= 0),
    previous_event_sha256 TEXT
        CHECK (previous_event_sha256 IS NULL OR length(previous_event_sha256) = 64),
    event_sha256 TEXT NOT NULL CHECK (length(event_sha256) = 64),
    actor_key_id TEXT,
    actor_roles_json TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    result TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    request_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    UNIQUE (project_id, event_sequence),
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (actor_key_id) REFERENCES api_keys(key_id)
);

CREATE TABLE audit_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    first_event_sequence INTEGER NOT NULL CHECK (first_event_sequence >= 0),
    last_event_sequence INTEGER NOT NULL CHECK (last_event_sequence >= first_event_sequence),
    terminal_event_sha256 TEXT NOT NULL CHECK (length(terminal_event_sha256) = 64),
    manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
    public_key_pem BLOB NOT NULL,
    signature BLOB NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE TABLE audit_exports (
    export_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    first_event_sequence INTEGER NOT NULL CHECK (first_event_sequence >= 0),
    last_event_sequence INTEGER NOT NULL CHECK (last_event_sequence >= first_event_sequence),
    manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
    bundle_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);
"""

_PROJECT_SCOPED_TABLES = (
    "review_cases",
    "review_inputs",
    "diagnosis_revisions",
    "verifier_runs",
    "human_decisions",
    "workflow_events",
    "idempotency_keys",
    "traces",
)

_PROJECT_INDEXED_TABLES = _PROJECT_SCOPED_TABLES + (
    "api_keys",
    "audit_events",
    "audit_checkpoints",
    "audit_exports",
)

_PROJECT_INDEXES_SQL = "\n".join(
    f"CREATE INDEX IF NOT EXISTS idx_{table}_project_id ON {table}(project_id);"
    for table in _PROJECT_INDEXED_TABLES
)


def _normalize_schema_sql(value: str) -> str:
    return " ".join(value.rstrip(";").split())


def _expected_schema(schema_sql: str) -> dict[str, str]:
    return {
        statement.strip().split()[2]: _normalize_schema_sql(statement.strip())
        for statement in schema_sql.split(";")
        if statement.strip()
    }


_EXPECTED_SCHEMA_V2_SQL = _expected_schema(_SCHEMA_V2_SQL)
_EXPECTED_SCHEMA_V3_SQL = _expected_schema(_SCHEMA_V3_SQL)


def _validate_schema(
    connection: sqlite3.Connection,
    *,
    expected: dict[str, str],
    version: int,
) -> None:
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    actual = {
        str(name): _normalize_schema_sql(str(sql))
        for name, sql in rows
        if sql is not None
    }
    if actual != expected:
        raise ReviewSchemaError(
            f"review schema structure does not match version {version}"
        )


def _execute_schema(connection: sqlite3.Connection, schema_sql: str) -> None:
    for statement in schema_sql.split(";"):
        if statement.strip():
            connection.execute(statement)


def _add_project_columns(connection: sqlite3.Connection) -> None:
    for table in _PROJECT_SCOPED_TABLES:
        connection.execute(
            f"ALTER TABLE {table} "
            "ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'"
        )


def _actual_schema(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {
        str(name): _normalize_schema_sql(str(sql))
        for name, sql in rows
        if sql is not None
    }


def _expected_schema_v4() -> dict[str, str]:
    connection = sqlite3.connect(":memory:")
    try:
        _execute_schema(connection, _SCHEMA_V3_SQL)
        _execute_schema(connection, _V4_TABLES_SQL)
        _add_project_columns(connection)
        _execute_schema(connection, _PROJECT_INDEXES_SQL)
        return _actual_schema(connection)
    finally:
        connection.close()


_EXPECTED_SCHEMA_V4_SQL = _expected_schema_v4()


def _seed_default_project(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO projects(project_id, name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (
            _DEFAULT_PROJECT_ID,
            _DEFAULT_PROJECT_NAME,
            _DEFAULT_PROJECT_TIMESTAMP,
            _DEFAULT_PROJECT_TIMESTAMP,
        ),
    )


def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
    _validate_schema(connection, expected=_EXPECTED_SCHEMA_V3_SQL, version=3)
    _execute_schema(connection, _V4_TABLES_SQL)
    _add_project_columns(connection)
    _seed_default_project(connection)
    _execute_schema(connection, _PROJECT_INDEXES_SQL)
    connection.execute(
        "UPDATE schema_metadata SET schema_version = ? WHERE singleton_key = 1",
        (SCHEMA_VERSION,),
    )


def _create_v4_schema(connection: sqlite3.Connection) -> None:
    _execute_schema(connection, _SCHEMA_V3_SQL)
    _execute_schema(connection, _V4_TABLES_SQL)
    _add_project_columns(connection)
    _seed_default_project(connection)
    _execute_schema(connection, _PROJECT_INDEXES_SQL)
    connection.execute(
        "INSERT INTO schema_metadata(singleton_key, schema_version) VALUES (1, ?)",
        (SCHEMA_VERSION,),
    )


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
    """Create schema v4, migrate exact v2/v3 databases, or verify exact v4 state."""
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
            if row == (LEGACY_SCHEMA_VERSION,):
                _validate_schema(
                    connection,
                    expected=_EXPECTED_SCHEMA_V2_SQL,
                    version=LEGACY_SCHEMA_VERSION,
                )
                _execute_schema(connection, _TRACE_SCHEMA_SQL)
                connection.execute(
                    "UPDATE schema_metadata SET schema_version = ? "
                    "WHERE singleton_key = 1",
                    (PREVIOUS_SCHEMA_VERSION,),
                )
                _migrate_v3_to_v4(connection)
            elif row == (PREVIOUS_SCHEMA_VERSION,):
                _migrate_v3_to_v4(connection)
            elif row == (SCHEMA_VERSION,):
                _validate_schema(
                    connection,
                    expected=_EXPECTED_SCHEMA_V4_SQL,
                    version=SCHEMA_VERSION,
                )
            else:
                raise ReviewSchemaError("unsupported review schema version")
        else:
            _create_v4_schema(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
