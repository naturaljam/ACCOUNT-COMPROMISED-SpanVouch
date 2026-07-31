import asyncio
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import cast

from pydantic import JsonValue, ValidationError

from spanvouch.adapters.storage.sqlite_schema import (
    connect_database,
    initialize_database,
)
from spanvouch.audit.chain import AuditChain, AuditEventInput
from spanvouch.audit.context import current_audit_context
from spanvouch.contracts.trace import TraceIR
from spanvouch.contracts.versioning import canonical_json
from spanvouch.review.errors import ReviewSchemaError
from spanvouch.trace.repository import TraceConflictError, TracePersistenceError


class SQLiteTraceRepository:
    """SQLite-backed trace repository with immutable trace identity."""

    def __init__(self, database: str | Path) -> None:
        value = os.fspath(database)
        if value == ":memory:" or value.startswith("file:"):
            raise ValueError(
                "trace database must be a filesystem path; "
                "SQLite memory databases and file: URIs are unsupported"
            )
        self._database = Path(value)
        self._audit_chain = AuditChain()

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    async def save(self, trace: TraceIR, *, project_id: str = "default") -> TraceIR:
        return await asyncio.to_thread(self._save, trace, project_id)

    async def get(self, trace_id: str, *, project_id: str = "default") -> TraceIR:
        return await asyncio.to_thread(self._get, trace_id, project_id)

    def _initialize(self) -> None:
        try:
            initialize_database(self._database)
        except ReviewSchemaError:
            raise
        except sqlite3.Error:
            raise TracePersistenceError("trace persistence operation failed") from None

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        try:
            connection = connect_database(self._database)
        except sqlite3.Error:
            raise TracePersistenceError("trace persistence operation failed") from None
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise TracePersistenceError("trace persistence operation failed") from None
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _save(self, trace: TraceIR, project_id: str) -> TraceIR:
        trace_json = canonical_json(trace)
        trace_sha256 = sha256(trace_json.encode("utf-8")).hexdigest()
        occurred_at = min(span.started_at for span in trace.spans)
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT trace_id, run_id, trace_json, trace_sha256 "
                "FROM traces WHERE trace_id = ? AND project_id = ?",
                (trace.trace_id, project_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO traces(project_id, trace_id, run_id, trace_json, trace_sha256) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        project_id,
                        trace.trace_id,
                        trace.run_id,
                        trace_json,
                        trace_sha256,
                    ),
                )
                self._record_audit(
                    connection,
                    project_id=project_id,
                    action="trace.ingest",
                    resource_type="trace",
                    resource_id=trace.trace_id,
                    result="created",
                    payload={"trace_id": trace.trace_id, "run_id": trace.run_id},
                    occurred_at=occurred_at,
                )
                return trace

            existing = self._decode_row(row, expected_trace_id=trace.trace_id)
            if existing != trace:
                raise TraceConflictError(f"trace_id conflict: {trace.trace_id}")
            self._record_audit(
                connection,
                project_id=project_id,
                action="trace.ingest",
                resource_type="trace",
                resource_id=trace.trace_id,
                result="replayed",
                payload={"trace_id": trace.trace_id, "run_id": trace.run_id},
                occurred_at=occurred_at,
            )
            return trace

    def _get(self, trace_id: str, project_id: str) -> TraceIR:
        with self._transaction(write=False) as connection:
            row = connection.execute(
                "SELECT trace_id, run_id, trace_json, trace_sha256 "
                "FROM traces WHERE trace_id = ? AND project_id = ?",
                (trace_id, project_id),
            ).fetchone()
            if row is None:
                raise KeyError(trace_id)
            return self._decode_row(row, expected_trace_id=trace_id)

    @staticmethod
    def _decode_row(row: sqlite3.Row, *, expected_trace_id: str) -> TraceIR:
        try:
            trace_json = str(row["trace_json"])
            stored_sha256 = str(row["trace_sha256"])
            if sha256(trace_json.encode("utf-8")).hexdigest() != stored_sha256:
                raise ValueError
            trace = TraceIR.model_validate_json(trace_json)
            if canonical_json(trace) != trace_json:
                raise ValueError
            if trace.trace_id != expected_trace_id or trace.trace_id != str(row["trace_id"]):
                raise ValueError
            if trace.run_id != str(row["run_id"]):
                raise ValueError
            return trace
        except (KeyError, TypeError, UnicodeError, ValidationError, ValueError):
            raise ValueError("stored trace data is invalid") from None

    def _record_audit(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        result: str,
        payload: dict[str, object],
        occurred_at: datetime,
    ) -> None:
        context = current_audit_context()
        if context is None:
            return
        self._audit_chain.append(
            connection,
            AuditEventInput(
                project_id=project_id,
                actor_key_id=context.actor_key_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                result=result,
                payload=cast(JsonValue, payload),
                occurred_at=occurred_at,
                request_id=context.request_id,
            ),
        )
