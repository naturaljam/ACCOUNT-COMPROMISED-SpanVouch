from fastapi import FastAPI

from afc.api.routes.health import router as health_router
from afc.api.routes.traces import build_trace_router
from afc.trace_ir.repository import InMemoryTraceRepository, TraceRepository


def create_app(trace_repository: TraceRepository | None = None) -> FastAPI:
    repository = trace_repository or InMemoryTraceRepository()
    application = FastAPI(title="Agent Failure Clinic", version="0.1.0")
    application.include_router(health_router)
    application.include_router(build_trace_router(repository))
    return application


app = create_app()
