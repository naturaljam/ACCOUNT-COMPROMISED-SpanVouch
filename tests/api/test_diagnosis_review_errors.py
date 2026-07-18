from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spanvouch.api.app import create_app
from spanvouch.diagnosis.errors import (
    DiagnosisUnavailableError,
    ProviderProtocolError,
    ProviderRequestError,
)
from spanvouch.review.errors import (
    ReviewConflictError,
    ReviewNotFoundError,
    ReviewPersistenceError,
    ReviewValidationError,
)
from spanvouch.review.workflow import ReviewWorkflowProviderError
from spanvouch.trace_ir.repository import InMemoryTraceRepository
from tests.diagnosis.test_trace_view import load_trace


@pytest.fixture(autouse=True)
def _disable_live_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


class FailingReviewService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def create(self, *args: object, **kwargs: object) -> object:
        raise self.error

    async def get(self, *args: object, **kwargs: object) -> object:
        raise self.error

    async def resume(self, *args: object, **kwargs: object) -> object:
        raise self.error

    async def decide(self, *args: object, **kwargs: object) -> object:
        raise self.error


class RecordingResumeService(FailingReviewService):
    def __init__(self) -> None:
        super().__init__(ReviewConflictError("stop after boundary"))
        self.resume_calls: list[tuple[str, bool]] = []

    async def resume(
        self, case_id: str, *, allow_live_api: bool = False
    ) -> object:
        self.resume_calls.append((case_id, allow_live_api))
        raise self.error


def _client(error: Exception, tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            trace_repository=InMemoryTraceRepository(),
            review_service=FailingReviewService(error),  # type: ignore[arg-type]
            review_database=tmp_path / "review.db",
        ),
        raise_server_exceptions=False,
    )


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_body"),
    [
        (ReviewNotFoundError("secret"), 404, {"detail": {"code": "review_not_found"}}),
        (ReviewConflictError("secret"), 409, {"detail": {"code": "review_conflict"}}),
        (ReviewValidationError("secret"), 422, {"detail": {"code": "review_invalid"}}),
        (
            ReviewPersistenceError("SQL /private/path"),
            500,
            {"detail": {"code": "internal_error"}},
        ),
        (
            RuntimeError("DEEPSEEK_API_KEY=secret"),
            500,
            {"detail": {"code": "internal_error"}},
        ),
    ],
)
def test_stable_sanitized_review_error_mapping(
    error: Exception,
    expected_status: int,
    expected_body: dict[str, object],
    tmp_path: Path,
) -> None:
    with _client(error, tmp_path) as client:
        response = client.get("/v1/diagnosis-reviews/case-1")

    assert response.status_code == expected_status
    assert response.json() == expected_body
    assert "secret" not in response.text
    assert "/private/path" not in response.text


@pytest.mark.parametrize(
    ("error", "expected_status", "expected"),
    [
        (
            ReviewWorkflowProviderError(
                "case-1", "provider_protocol_error", retryable=False
            ),
            502,
            {
                "code": "provider_protocol_error",
                "case_id": "case-1",
                "retryable": False,
            },
        ),
        (
            ReviewWorkflowProviderError(
                "case-1", "provider_not_configured", retryable=False
            ),
            503,
            {
                "code": "provider_not_configured",
                "case_id": "case-1",
                "retryable": False,
            },
        ),
        (
            ReviewWorkflowProviderError("case-1", "transport_error", retryable=True),
            503,
            {"code": "transport_error", "case_id": "case-1", "retryable": True},
        ),
        (
            ReviewWorkflowProviderError(
                "case-1", "revision_provider_failed", retryable=True
            ),
            503,
            {
                "code": "revision_provider_failed",
                "case_id": "case-1",
                "retryable": True,
            },
        ),
    ],
)
def test_durable_provider_errors_have_minimal_stable_body(
    error: Exception,
    expected_status: int,
    expected: dict[str, object],
    tmp_path: Path,
) -> None:
    with _client(error, tmp_path) as client:
        response = client.post("/v1/diagnosis-reviews/case-1/resume")

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected}


@pytest.mark.parametrize(
    ("body", "expected"),
    ((None, False), ({}, False), ({"allow_live_api": True}, True)),
)
def test_resume_consent_is_explicit_at_the_http_application_boundary(
    body: dict[str, object] | None,
    expected: bool,
    tmp_path: Path,
) -> None:
    service = RecordingResumeService()
    application = create_app(
        trace_repository=InMemoryTraceRepository(),
        review_service=service,  # type: ignore[arg-type]
        review_database=tmp_path / "review.db",
    )
    with TestClient(application) as client:
        if body is None:
            response = client.post("/v1/diagnosis-reviews/case-1/resume")
        else:
            response = client.post("/v1/diagnosis-reviews/case-1/resume", json=body)

    assert response.status_code == 409
    assert service.resume_calls == [("case-1", expected)]


@pytest.mark.parametrize(
    ("error", "expected_status", "code", "retryable"),
    [
        (DiagnosisUnavailableError("secret"), 503, "diagnoser_unavailable", False),
        (ProviderProtocolError("secret"), 502, "provider_protocol_error", False),
        (
            ProviderRequestError("transport_error", retryable=True),
            503,
            "transport_error",
            True,
        ),
        (
            ProviderRequestError("upstream_http_error", retryable=False),
            502,
            "upstream_http_error",
            False,
        ),
    ],
)
def test_initial_diagnosis_provider_errors_are_sanitized(
    error: Exception,
    expected_status: int,
    code: str,
    retryable: bool,
    tmp_path: Path,
) -> None:
    client = _client(error, tmp_path)
    trace = load_trace("clean-01")
    with client:
        assert client.post("/v1/traces", json=trace.model_dump(mode="json")).status_code == 201
        response = client.post(
            f"/v1/traces/{trace.trace_id}/diagnosis-reviews",
            json={"idempotency_key": "create-error"},
        )

    assert response.status_code == expected_status
    assert response.json() == {"detail": {"code": code, "retryable": retryable}}


def test_unknown_provider_error_code_is_not_reflected(tmp_path: Path) -> None:
    client = _client(
        ProviderRequestError("DEEPSEEK_API_KEY=secret", retryable=False), tmp_path
    )
    trace = load_trace("clean-01")
    with client:
        assert client.post("/v1/traces", json=trace.model_dump(mode="json")).status_code == 201
        response = client.post(
            f"/v1/traces/{trace.trace_id}/diagnosis-reviews",
            json={"idempotency_key": "create-error"},
        )

    assert response.status_code == 502
    assert response.json() == {
        "detail": {"code": "provider_request_error", "retryable": False}
    }
    assert "secret" not in response.text


def test_missing_trace_is_404_before_review_service(tmp_path: Path) -> None:
    with _client(RuntimeError("must not be called"), tmp_path) as client:
        response = client.post(
            "/v1/traces/missing/diagnosis-reviews",
            json={"idempotency_key": "missing-trace"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "trace_not_found"}}


def test_injected_review_service_does_not_initialize_an_unrelated_database(
    tmp_path: Path,
) -> None:
    unreachable = tmp_path / "missing-parent" / "review.db"
    app = create_app(
        trace_repository=InMemoryTraceRepository(),
        review_service=FailingReviewService(ReviewNotFoundError("missing")),  # type: ignore[arg-type]
        review_database=unreachable,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/v1/diagnosis-reviews/case-1")

    assert response.status_code == 404
    assert not unreachable.exists()
