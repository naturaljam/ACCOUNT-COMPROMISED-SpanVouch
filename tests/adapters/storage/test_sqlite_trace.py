import asyncio
import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from spanvouch.adapters.storage.sqlite_schema import connect_database
from spanvouch.adapters.storage.sqlite_trace import SQLiteTraceRepository
from spanvouch.contracts.trace import TraceIR, TraceSpan
from spanvouch.contracts.versioning import canonical_bytes, canonical_json
from spanvouch.trace.repository import TraceConflictError


def _trace(*, trace_id: str = "trace-sqlite-1", run_id: str = "run-1") -> TraceIR:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    return TraceIR(
        trace_id=trace_id,
        run_id=run_id,
        spans=[
            TraceSpan(
                trace_id=trace_id,
                span_id="root",
                name="supportlab.run",
                kind="agent",
                status="ok",
                started_at=now,
                ended_at=now,
                attributes={"sequence": 1, "message": "durable trace"},
            )
        ],
    )


async def _open_repository(database: Path) -> SQLiteTraceRepository:
    repository = SQLiteTraceRepository(database)
    await repository.initialize()
    return repository


async def test_save_reopen_and_get_preserves_canonical_trace_bytes(tmp_path: Path) -> None:
    database = tmp_path / "spanvouch.db"
    trace = _trace()
    repository = await _open_repository(database)

    assert await repository.save(trace) == trace

    reopened = await _open_repository(database)
    restored = await reopened.get(trace.trace_id)
    assert restored == trace
    assert canonical_bytes(restored) == canonical_bytes(trace)

    with connect_database(database) as connection:
        row = connection.execute(
            "SELECT run_id, trace_json, trace_sha256 FROM traces WHERE trace_id = ?",
            (trace.trace_id,),
        ).fetchone()
    expected_json = canonical_json(trace)
    assert row == (trace.run_id, expected_json, sha256(expected_json.encode()).hexdigest())


async def test_save_allows_idempotent_retry_across_repository_instances(
    tmp_path: Path,
) -> None:
    database = tmp_path / "spanvouch.db"
    trace = _trace()
    first = await _open_repository(database)
    second = await _open_repository(database)

    assert await first.save(trace) == trace
    assert await second.save(trace) == trace


async def test_save_rejects_different_content_for_existing_trace_id(
    tmp_path: Path,
) -> None:
    repository = await _open_repository(tmp_path / "spanvouch.db")
    await repository.save(_trace())

    with pytest.raises(TraceConflictError, match="trace_id conflict: trace-sqlite-1"):
        await repository.save(_trace(run_id="run-2"))


async def test_get_missing_trace_preserves_repository_key_error(tmp_path: Path) -> None:
    repository = await _open_repository(tmp_path / "spanvouch.db")

    with pytest.raises(KeyError, match="missing"):
        await repository.get("missing")


@pytest.mark.parametrize("corruption", ["hash", "json", "run_id"])
async def test_get_rejects_corrupted_stored_trace(
    tmp_path: Path,
    corruption: str,
) -> None:
    database = tmp_path / "spanvouch.db"
    trace = _trace()
    repository = await _open_repository(database)
    await repository.save(trace)

    with connect_database(database) as connection:
        if corruption == "hash":
            connection.execute(
                "UPDATE traces SET trace_sha256 = ? WHERE trace_id = ?",
                ("0" * 64, trace.trace_id),
            )
        elif corruption == "json":
            invalid_json = "{}"
            connection.execute(
                "UPDATE traces SET trace_json = ?, trace_sha256 = ? WHERE trace_id = ?",
                (invalid_json, sha256(invalid_json.encode()).hexdigest(), trace.trace_id),
            )
        else:
            connection.execute(
                "UPDATE traces SET run_id = ? WHERE trace_id = ?",
                ("wrong-run", trace.trace_id),
            )

    with pytest.raises(ValueError, match="stored trace data is invalid"):
        await repository.get(trace.trace_id)


async def test_two_repository_instances_serialize_identical_concurrent_saves(
    tmp_path: Path,
) -> None:
    database = tmp_path / "spanvouch.db"
    first = await _open_repository(database)
    second = await _open_repository(database)
    trace = _trace()

    saved = await asyncio.gather(first.save(trace), second.save(trace))

    assert saved == [trace, trace]
    assert await first.get(trace.trace_id) == trace


async def test_two_repository_instances_reject_one_concurrent_conflicting_save(
    tmp_path: Path,
) -> None:
    database = tmp_path / "spanvouch.db"
    first = await _open_repository(database)
    second = await _open_repository(database)
    traces = (_trace(run_id="run-1"), _trace(run_id="run-2"))

    results = await asyncio.gather(
        first.save(traces[0]), second.save(traces[1]), return_exceptions=True
    )

    assert sum(isinstance(result, TraceIR) for result in results) == 1
    assert sum(isinstance(result, TraceConflictError) for result in results) == 1
    assert await first.get("trace-sqlite-1") in traces


def test_database_rejects_invalid_trace_hash_length(tmp_path: Path) -> None:
    database = tmp_path / "spanvouch.db"
    asyncio.run(_open_repository(database))

    with (
        connect_database(database) as connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        connection.execute(
            "INSERT INTO traces(trace_id, run_id, trace_json, trace_sha256) "
            "VALUES ('trace-1', 'run-1', '{}', 'short')"
        )
