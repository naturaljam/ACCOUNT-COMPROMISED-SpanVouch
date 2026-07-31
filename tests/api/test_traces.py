from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spanvouch.api.app import create_app
from spanvouch.contracts.trace import TraceIR
from spanvouch.trace.repository import InMemoryTraceRepository
from tests.api.helpers import make_project_client


class ValueErrorTraceRepository:
    async def save(self, trace: TraceIR) -> TraceIR:
        raise ValueError("unexpected repository failure")

    async def get(self, trace_id: str) -> TraceIR:
        raise KeyError(trace_id)


class RecordingTraceRepository:
    def __init__(self) -> None:
        self.saved: list[TraceIR] = []

    async def save(self, trace: TraceIR) -> TraceIR:
        self.saved.append(trace)
        return trace

    async def get(self, trace_id: str) -> TraceIR:
        for trace in self.saved:
            if trace.trace_id == trace_id:
                return trace
        raise KeyError(trace_id)


def trace_span_payload(span_id: str, parent_span_id: str | None = None) -> dict[str, object]:
    now = datetime(2026, 7, 15, tzinfo=UTC).isoformat()
    return {
        "trace_id": "trace-api-1",
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": "supportlab.run",
        "kind": "agent",
        "status": "ok",
        "started_at": now,
        "ended_at": now,
        "attributes": {},
    }


def valid_trace_payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "trace_id": "trace-api-1",
        "run_id": "run-api-1",
        "spans": [trace_span_payload("root")],
    }


def test_trace_ingestion_returns_created_summary() -> None:
    context = make_project_client(trace_repository=InMemoryTraceRepository())
    client = context.client

    response = client.post(
        "/v1/traces",
        json=valid_trace_payload(),
        headers=context.headers,
    )

    assert response.status_code == 201
    assert response.json() == {
        "trace_id": "trace-api-1",
        "run_id": "run-api-1",
        "span_count": 1,
    }


def test_trace_ingestion_allows_idempotent_retry() -> None:
    context = make_project_client(trace_repository=InMemoryTraceRepository())
    client = context.client
    payload = valid_trace_payload()

    first_response = client.post("/v1/traces", json=payload, headers=context.headers)
    retry_response = client.post("/v1/traces", json=payload, headers=context.headers)

    assert first_response.status_code == 201
    assert retry_response.status_code == 201
    assert retry_response.json() == first_response.json()


def test_default_trace_repository_persists_across_api_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "spanvouch.db"
    monkeypatch.setenv("SPANVOUCH_DB_PATH", str(database))
    context = make_project_client(database=database)
    payload = valid_trace_payload()

    with TestClient(
        create_app(
            project_repository=context.repository,
            review_database=database,
        )
    ) as first_client:
        assert (
            first_client.post("/v1/traces", json=payload, headers=context.headers).status_code
            == 201
        )

    with TestClient(
        create_app(
            project_repository=context.repository,
            review_database=database,
        )
    ) as restarted_client:
        diagnosis = restarted_client.post(
            "/v1/traces/trace-api-1/diagnoses",
            json={},
            headers=context.headers,
        )
        conflicting = dict(payload)
        conflicting["run_id"] = "run-api-2"
        conflict = restarted_client.post(
            "/v1/traces", json=conflicting, headers=context.headers
        )

    assert diagnosis.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "trace_id conflict: trace-api-1"}


def test_trace_ingestion_returns_conflict_for_different_content_with_same_id() -> None:
    context = make_project_client(trace_repository=InMemoryTraceRepository())
    client = context.client
    first_payload = valid_trace_payload()
    conflicting_payload = valid_trace_payload()
    conflicting_payload["run_id"] = "run-api-2"

    first_response = client.post(
        "/v1/traces", json=first_payload, headers=context.headers
    )
    conflict_response = client.post(
        "/v1/traces", json=conflicting_payload, headers=context.headers
    )

    assert first_response.status_code == 201
    assert conflict_response.status_code == 409
    assert conflict_response.json() == {"detail": "trace_id conflict: trace-api-1"}


def test_trace_ingestion_does_not_map_unrelated_value_error_to_conflict() -> None:
    context = make_project_client(trace_repository=ValueErrorTraceRepository())
    client = TestClient(context.client.app, raise_server_exceptions=False)

    response = client.post("/v1/traces", json=valid_trace_payload(), headers=context.headers)

    assert response.status_code == 500


def test_trace_ingestion_rejects_orphan_span() -> None:
    payload = valid_trace_payload()
    spans = payload["spans"]
    assert isinstance(spans, list)
    assert isinstance(spans[0], dict)
    spans[0]["parent_span_id"] = "missing"
    context = make_project_client(trace_repository=InMemoryTraceRepository())
    client = context.client

    response = client.post("/v1/traces", json=payload, headers=context.headers)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "spans",
    [
        [trace_span_payload("first", "second"), trace_span_payload("second", "first")],
        [
            trace_span_payload("first", "third"),
            trace_span_payload("second", "first"),
            trace_span_payload("third", "second"),
        ],
        [trace_span_payload("first-root"), trace_span_payload("second-root")],
    ],
    ids=["two-span-cycle", "multi-span-cycle", "multiple-roots"],
)
def test_trace_ingestion_rejects_malformed_span_graph_without_saving(
    spans: list[dict[str, object]],
) -> None:
    payload = valid_trace_payload()
    payload["spans"] = spans
    repository = RecordingTraceRepository()
    context = make_project_client(trace_repository=repository)
    client = context.client

    response = client.post("/v1/traces", json=payload, headers=context.headers)

    assert response.status_code == 422
    assert repository.saved == []
