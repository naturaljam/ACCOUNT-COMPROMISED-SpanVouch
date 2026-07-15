from datetime import UTC, datetime

from fastapi.testclient import TestClient

from afc.api.app import create_app
from afc.trace_ir.repository import InMemoryTraceRepository


def valid_trace_payload() -> dict[str, object]:
    now = datetime(2026, 7, 15, tzinfo=UTC).isoformat()
    return {
        "schema_version": "1.0",
        "trace_id": "trace-api-1",
        "run_id": "run-api-1",
        "spans": [
            {
                "trace_id": "trace-api-1",
                "span_id": "root",
                "parent_span_id": None,
                "name": "supportlab.run",
                "kind": "agent",
                "status": "ok",
                "started_at": now,
                "ended_at": now,
                "attributes": {},
            }
        ],
    }


def test_trace_ingestion_returns_created_summary() -> None:
    app = create_app(trace_repository=InMemoryTraceRepository())
    client = TestClient(app)

    response = client.post("/v1/traces", json=valid_trace_payload())

    assert response.status_code == 201
    assert response.json() == {
        "trace_id": "trace-api-1",
        "run_id": "run-api-1",
        "span_count": 1,
    }


def test_trace_ingestion_rejects_orphan_span() -> None:
    payload = valid_trace_payload()
    spans = payload["spans"]
    assert isinstance(spans, list)
    assert isinstance(spans[0], dict)
    spans[0]["parent_span_id"] = "missing"
    client = TestClient(create_app(trace_repository=InMemoryTraceRepository()))

    response = client.post("/v1/traces", json=payload)

    assert response.status_code == 422
