from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from spanvouch.api.auth import require_project_capability
from spanvouch.contracts.trace import TraceIR
from spanvouch.projects.models import ProjectContext
from spanvouch.security.policy import Capability
from spanvouch.trace.repository import TraceConflictError, TraceRepository

_REQUIRE_INGEST_TRACE = require_project_capability(Capability.INGEST_TRACE)


class TraceCreated(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    trace_id: str
    run_id: str
    span_count: int


def build_trace_router(repository: TraceRepository) -> APIRouter:
    router = APIRouter(prefix="/v1/traces", tags=["traces"])

    @router.post("", response_model=TraceCreated, status_code=status.HTTP_201_CREATED)
    async def create_trace(
        trace: TraceIR,
        context: Annotated[ProjectContext, Depends(_REQUIRE_INGEST_TRACE)],
    ) -> TraceCreated:
        try:
            saved = await repository.save(trace, project_id=context.project_id)
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
