import pytest
from fastapi.testclient import TestClient

from afc.api.app import create_app
from afc.diagnosis.errors import ProviderRequestError
from afc.diagnosis.models import DiagnoserKind
from afc.diagnosis.service import DiagnosisService
from afc.trace_ir.repository import InMemoryTraceRepository
from tests.diagnosis.test_trace_view import load_trace


def client_with_repository() -> TestClient:
    return TestClient(create_app(trace_repository=InMemoryTraceRepository()))


def ingest(client: TestClient, run_id: str) -> str:
    trace = load_trace(run_id)
    response = client.post("/v1/traces", json=trace.model_dump(mode="json"))
    assert response.status_code == 201
    return trace.trace_id


def test_diagnosis_api_defaults_to_rule_only() -> None:
    client = client_with_repository()
    trace_id = ingest(client, "invalid_argument-01")

    response = client.post(f"/v1/traces/{trace_id}/diagnoses", json={})

    assert response.status_code == 200
    assert response.json()["diagnoser"] == "rules"
    assert response.json()["failure_type"] == "invalid_argument"


def test_diagnosis_api_returns_semantic_abstain_as_200() -> None:
    client = client_with_repository()
    trace_id = ingest(client, "missing_precondition-01")

    response = client.post(f"/v1/traces/{trace_id}/diagnoses", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "abstained"
    assert response.json()["abstain_reason"] == "unsupported_failure_type"


def test_diagnosis_api_maps_missing_trace_and_invalid_request() -> None:
    client = client_with_repository()

    missing = client.post("/v1/traces/missing/diagnoses", json={})
    invalid = client.post(
        "/v1/traces/missing/diagnoses", json={"diagnoser": "unknown"}
    )

    assert missing.status_code == 404
    assert invalid.status_code == 422


def test_deepseek_must_be_explicitly_configured() -> None:
    client = client_with_repository()
    trace_id = ingest(client, "clean-01")

    response = client.post(
        f"/v1/traces/{trace_id}/diagnoses", json={"diagnoser": "deepseek"}
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "diagnoser_unavailable"}


def test_idempotency_key_conflict_maps_to_409() -> None:
    client = client_with_repository()
    first = ingest(client, "clean-01")
    second = ingest(client, "clean-02")
    body = {"idempotency_key": "same-key"}

    assert client.post(f"/v1/traces/{first}/diagnoses", json=body).status_code == 200
    conflict = client.post(f"/v1/traces/{second}/diagnoses", json=body)

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
    service = DiagnosisService(
        {DiagnoserKind.DEEPSEEK: FailingDiagnoser(retryable=retryable)}  # type: ignore[dict-item]
    )
    client = TestClient(
        create_app(trace_repository=repository, diagnosis_service=service)
    )
    trace_id = ingest(client, "clean-01")

    response = client.post(
        f"/v1/traces/{trace_id}/diagnoses", json={"diagnoser": "deepseek"}
    )

    assert response.status_code == expected
    assert response.json() == {"detail": "provider_unavailable"}
