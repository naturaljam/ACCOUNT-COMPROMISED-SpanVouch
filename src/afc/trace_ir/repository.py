from typing import Protocol

from afc.trace_ir.models import TraceIR


class TraceConflictError(ValueError):
    """Raised when a trace ID is reused for different trace content."""


class TraceRepository(Protocol):
    async def save(self, trace: TraceIR) -> TraceIR: ...
    async def get(self, trace_id: str) -> TraceIR: ...


class InMemoryTraceRepository:
    def __init__(self) -> None:
        self._traces: dict[str, TraceIR] = {}

    async def save(self, trace: TraceIR) -> TraceIR:
        existing = self._traces.get(trace.trace_id)
        if existing is not None and existing != trace:
            raise TraceConflictError(f"trace_id conflict: {trace.trace_id}")
        self._traces[trace.trace_id] = trace
        return trace

    async def get(self, trace_id: str) -> TraceIR:
        return self._traces[trace_id]
