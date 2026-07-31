from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from spanvouch.api.auth import require_capability, require_project_capability
from spanvouch.contracts.diagnosis import DiagnoserKind
from spanvouch.contracts.review import (
    DiagnosisReviewDetail,
    HumanDecisionDraft,
)
from spanvouch.contracts.verification import (
    VerificationMode,
)
from spanvouch.diagnosis.errors import (
    DiagnosisUnavailableError,
    ProviderConfigurationError,
    ProviderProtocolError,
    ProviderRequestError,
)
from spanvouch.projects.models import ProjectContext
from spanvouch.review.application import ReviewApplication
from spanvouch.review.errors import (
    ReviewConflictError,
    ReviewNotFoundError,
    ReviewPersistenceError,
    ReviewSchemaError,
    ReviewValidationError,
    ReviewWorkflowProviderError,
)
from spanvouch.security.identity import Principal
from spanvouch.security.policy import Capability
from spanvouch.trace.repository import TraceRepository

_REQUIRE_CREATE_REVIEW = require_project_capability(Capability.CREATE_REVIEW)
_REQUIRE_READ_PROJECT = require_capability(Capability.READ_PROJECT)
_REQUIRE_RESUME_REVIEW = require_capability(Capability.RESUME_REVIEW)
_REQUIRE_DECIDE_REVIEW = require_capability(Capability.DECIDE_REVIEW)

_REQUEST_ERROR_CODES = {
    "transport_error",
    "upstream_http_error",
    "missing_response",
}
_WORKFLOW_PROVIDER_CODES = _REQUEST_ERROR_CODES | {
    "provider_not_configured",
    "provider_protocol_error",
    "provider_request_error",
    "provider_error",
    "revision_provider_failed",
}


def _detail(status_code: int, content: dict[str, object]) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": content})


class DiagnosisReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagnoser: DiagnoserKind = DiagnoserKind.RULES
    verifier: VerificationMode = VerificationMode.DETERMINISTIC
    idempotency_key: str = Field(min_length=1, max_length=200)


class DiagnosisReviewDecisionRequest(HumanDecisionDraft):
    idempotency_key: str = Field(min_length=1, max_length=200)

    def decision(self) -> HumanDecisionDraft:
        return HumanDecisionDraft.model_validate(
            self.model_dump(exclude={"idempotency_key"})
        )


class DiagnosisReviewResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_live_api: bool = False


def _error_response(error: Exception) -> JSONResponse:
    if isinstance(error, ReviewNotFoundError):
        return _detail(404, {"code": "review_not_found"})
    if isinstance(error, ReviewConflictError):
        return _detail(409, {"code": "review_conflict"})
    if isinstance(error, ReviewValidationError):
        return _detail(422, {"code": "review_invalid"})
    if isinstance(error, ReviewWorkflowProviderError):
        code = (
            error.code if error.code in _WORKFLOW_PROVIDER_CODES else "provider_error"
        )
        unavailable = error.retryable or code in {
            "provider_not_configured",
            "missing_response",
            "transport_error",
        }
        return _detail(
            503 if unavailable else 502,
            {
                "code": code,
                "case_id": error.case_id,
                "retryable": error.retryable,
            },
        )
    if isinstance(error, (DiagnosisUnavailableError, ProviderConfigurationError)):
        return _detail(
            503,
            {"code": "diagnoser_unavailable", "retryable": False},
        )
    if isinstance(error, ProviderRequestError):
        code = (
            error.code if error.code in _REQUEST_ERROR_CODES else "provider_request_error"
        )
        return _detail(
            503 if error.retryable else 502,
            {"code": code, "retryable": error.retryable},
        )
    if isinstance(error, ProviderProtocolError):
        return _detail(
            502,
            {"code": "provider_protocol_error", "retryable": False},
        )
    if isinstance(error, (ReviewPersistenceError, ReviewSchemaError)):
        return _detail(500, {"code": "internal_error"})
    return _detail(500, {"code": "internal_error"})


def build_diagnosis_review_router(
    trace_repository: TraceRepository,
    review_service: ReviewApplication,
) -> APIRouter:
    router = APIRouter(tags=["diagnosis-reviews"])

    @router.post(
        "/v1/traces/{trace_id}/diagnosis-reviews",
        response_model=DiagnosisReviewDetail,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_diagnosis_review(
        trace_id: str,
        request: DiagnosisReviewRequest,
        context: Annotated[ProjectContext, Depends(_REQUIRE_CREATE_REVIEW)],
    ) -> DiagnosisReviewDetail | JSONResponse:
        try:
            trace = await trace_repository.get(trace_id, project_id=context.project_id)
        except KeyError:
            return _detail(404, {"code": "trace_not_found"})
        try:
            return await review_service.create(
                trace,
                diagnoser=request.diagnoser,
                verification_mode=request.verifier,
                idempotency_key=request.idempotency_key,
            )
        except Exception as error:
            return _error_response(error)

    @router.get(
        "/v1/diagnosis-reviews/{case_id}", response_model=DiagnosisReviewDetail
    )
    async def get_diagnosis_review(
        case_id: str,
        _: Annotated[Principal, Depends(_REQUIRE_READ_PROJECT)],
    ) -> DiagnosisReviewDetail | JSONResponse:
        try:
            return await review_service.get(case_id)
        except Exception as error:
            return _error_response(error)

    @router.post(
        "/v1/diagnosis-reviews/{case_id}/resume",
        response_model=DiagnosisReviewDetail,
    )
    async def resume_diagnosis_review(
        case_id: str,
        _: Annotated[Principal, Depends(_REQUIRE_RESUME_REVIEW)],
        request: DiagnosisReviewResumeRequest | None = None,
    ) -> DiagnosisReviewDetail | JSONResponse:
        try:
            return await review_service.resume(
                case_id,
                allow_live_api=request.allow_live_api if request is not None else False,
            )
        except Exception as error:
            return _error_response(error)

    @router.post(
        "/v1/diagnosis-reviews/{case_id}/decisions",
        response_model=DiagnosisReviewDetail,
    )
    async def decide_diagnosis_review(
        case_id: str,
        request: DiagnosisReviewDecisionRequest,
        _: Annotated[Principal, Depends(_REQUIRE_DECIDE_REVIEW)],
    ) -> DiagnosisReviewDetail | JSONResponse:
        try:
            return await review_service.decide(
                case_id,
                request.decision(),
                idempotency_key=request.idempotency_key,
            )
        except Exception as error:
            return _error_response(error)

    return router
