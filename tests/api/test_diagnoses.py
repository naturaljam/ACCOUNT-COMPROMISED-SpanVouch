import pytest
from fastapi.testclient import TestClient

from spanvouch.contracts.diagnosis import DiagnoserKind
from spanvouch.diagnosis.engine import DiagnosisEngine
from spanvouch.diagnosis.errors import ProviderRequestError
from spanvouch.trace.repository import InMemoryTraceRepository
from tests.api.helpers import make_project_client
from tests.trace.test_diagnostic_view import load_trace


def client_with_repository() -> tuple[TestClient, dict[str, str]]:
    context = make_project_client(trace_repository=InMemoryTraceRepository())
    return context.client, context.headers


def ingest(client: TestClient, headers: dict[str, str], run_id: str) -> str:
    trace = load_trace(run_id)
    response = client.post(
        "/v1/traces", json=trace.model_dump(mode="json"), headers=headers
    )
    assert response.status_code == 201
    return trace.trace_id


def test_diagnosis_api_defaults_to_rule_only() -> None:
    client, headers = client_with_repository()
    trace_id = ingest(client, headers, "invalid_argument-01")

    response = client.post(f"/v1/traces/{trace_id}/diagnoses", json={}, headers=headers)

    assert response.status_code == 200
    assert response.json()["diagnoser"] == "rules"
    assert response.json()["failure_type"] == "invalid_argument"


def test_diagnosis_api_returns_semantic_abstain_as_200() -> None:
    client, headers = client_with_repository()
    trace_id = ingest(client, headers, "missing_precondition-01")

    response = client.post(f"/v1/traces/{trace_id}/diagnoses", json={}, headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "abstained"
    assert response.json()["abstain_reason"] == "unsupported_failure_type"


def test_diagnosis_api_maps_missing_trace_and_invalid_request() -> None:
    client, headers = client_with_repository()

    missing = client.post("/v1/traces/missing/diagnoses", json={}, headers=headers)
    invalid = client.post(
        "/v1/traces/missing/diagnoses", json={"diagnoser": "unknown"}, headers=headers
    )

    assert missing.status_code == 404
    assert invalid.status_code == 422


def test_deepseek_must_be_explicitly_configured() -> None:
    client, headers = client_with_repository()
    trace_id = ingest(client, headers, "clean-01")

    response = client.post(
        f"/v1/traces/{trace_id}/diagnoses",
        json={"diagnoser": "deepseek"},
        headers=headers,
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "diagnoser_unavailable"}


def test_idempotency_key_conflict_maps_to_409() -> None:
    client, headers = client_with_repository()
    first = ingest(client, headers, "clean-01")
    second = ingest(client, headers, "clean-02")
    body = {"idempotency_key": "same-key"}

    assert (
        client.post(f"/v1/traces/{first}/diagnoses", json=body, headers=headers).status_code
        == 200
    )
    conflict = client.post(f"/v1/traces/{second}/diagnoses", json=body, headers=headers)

    assert conflict.status_code == 409


class FailingDiagnoser:
    version_fingerprint = "failing-v1"

    def __init__(self, *, retryable: bool) -> None:
        self.retryable = retryable

    async def diagnose(self, view: object, evidence: object) -> object:
        raise ProviderRequestError(
            "upstream_http_error",
            status_code=503 if self.retryable else 401,
            retryable=self.retryable,
        )


@pytest.mark.parametrize(("retryable", "expected"), [(False, 502), (True, 503)])
def test_provider_error_mapping(retryable: bool, expected: int) -> None:
    repository = InMemoryTraceRepository()
    service = DiagnosisEngine(
        {DiagnoserKind.DEEPSEEK: FailingDiagnoser(retryable=retryable)}  # type: ignore[dict-item]
    )
    context = make_project_client(
        trace_repository=repository,
        diagnosis_service=service,
    )
    client = context.client
    headers = context.headers
    trace_id = ingest(client, headers, "clean-01")

    response = client.post(
        f"/v1/traces/{trace_id}/diagnoses",
        json={"diagnoser": "deepseek"},
        headers=headers,
    )

    assert response.status_code == expected
    assert response.json() == {"detail": "provider_unavailable"}
