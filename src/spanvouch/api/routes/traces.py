from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from spanvouch.trace_ir.models import TraceIR
from spanvouch.trace_ir.repository import TraceConflictError, TraceRepository


class TraceCreated(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    trace_id: str
    run_id: str
    span_count: int


def build_trace_router(repository: TraceRepository) -> APIRouter:
    router = APIRouter(prefix="/v1/traces", tags=["traces"])

    @router.post("", response_model=TraceCreated, status_code=status.HTTP_201_CREATED)
    async def create_trace(trace: TraceIR) -> TraceCreated:
        try:
            saved = await repository.save(trace)
        except TraceConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        return TraceCreated(
            trace_id=saved.trace_id,
            run_id=saved.run_id,
            span_count=len(saved.spans),
        )

    return router
