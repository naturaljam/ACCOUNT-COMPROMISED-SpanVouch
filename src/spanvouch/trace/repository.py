from typing import Protocol

from spanvouch.contracts.trace import TraceIR


class TraceConflictError(ValueError):
    """Raised when a trace ID is reused for different trace content."""


class TracePersistenceError(RuntimeError):
    """Raised when durable trace storage cannot complete safely."""


class TraceRepository(Protocol):
    async def save(self, trace: TraceIR, *, project_id: str = "default") -> TraceIR: ...
    async def get(self, trace_id: str, *, project_id: str = "default") -> TraceIR: ...


class InMemoryTraceRepository:
    def __init__(self) -> None:
        self._traces: dict[tuple[str, str], TraceIR] = {}

    async def save(self, trace: TraceIR, *, project_id: str = "default") -> TraceIR:
        key = (project_id, trace.trace_id)
        existing = self._traces.get(key)
        if existing is not None and existing != trace:
            raise TraceConflictError(f"trace_id conflict: {trace.trace_id}")
        self._traces[key] = trace
        return trace

    async def get(self, trace_id: str, *, project_id: str = "default") -> TraceIR:
        return self._traces[(project_id, trace_id)]
