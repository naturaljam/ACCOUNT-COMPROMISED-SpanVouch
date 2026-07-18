from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from afc.diagnosis.errors import (
    DiagnosisConflictError,
    DiagnosisUnavailableError,
    ProviderConfigurationError,
    ProviderProtocolError,
    ProviderRequestError,
)
from afc.diagnosis.models import DiagnoserKind, DiagnosisReport
from afc.diagnosis.service import DiagnosisService
from afc.trace_ir.repository import TraceRepository


class DiagnosisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagnoser: DiagnoserKind = DiagnoserKind.RULES
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


def build_diagnosis_router(
    repository: TraceRepository, service: DiagnosisService
) -> APIRouter:
    router = APIRouter(prefix="/v1/traces", tags=["diagnoses"])

    @router.post("/{trace_id}/diagnoses", response_model=DiagnosisReport)
    async def create_diagnosis(
        trace_id: str, request: DiagnosisRequest
    ) -> DiagnosisReport:
        try:
            trace = await repository.get(trace_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="trace_not_found"
            ) from exc
        try:
            return await service.diagnose(
                trace,
                request.diagnoser,
                idempotency_key=request.idempotency_key,
            )
        except DiagnosisConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="idempotency_conflict",
            ) from exc
        except (DiagnosisUnavailableError, ProviderConfigurationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="diagnoser_unavailable",
            ) from exc
        except ProviderRequestError as exc:
            upstream_status = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if exc.retryable
                else status.HTTP_502_BAD_GATEWAY
            )
            raise HTTPException(
                status_code=upstream_status,
                detail="provider_unavailable",
            ) from exc
        except ProviderProtocolError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="provider_unavailable",
            ) from exc

    return router
