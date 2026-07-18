import asyncio
import json
from collections.abc import Mapping
from hashlib import sha256

from spanvouch.contracts.diagnosis import DiagnoserKind, DiagnosisReport
from spanvouch.contracts.trace import TraceIR
from spanvouch.diagnosis.errors import DiagnosisConflictError, DiagnosisUnavailableError
from spanvouch.diagnosis.protocols import Diagnoser
from spanvouch.trace.diagnostic_view import TraceProjector
from spanvouch.trace.evidence_catalog import EvidenceCatalog


class DiagnosisEngine:
    def __init__(self, diagnosers: Mapping[DiagnoserKind, Diagnoser]) -> None:
        self._diagnosers = dict(diagnosers)
        self._completed: dict[str, DiagnosisReport] = {}
        self._inflight: dict[str, asyncio.Task[DiagnosisReport]] = {}
        self._idempotency_fingerprints: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def diagnose(
        self,
        trace: TraceIR,
        kind: DiagnoserKind = DiagnoserKind.RULES,
        *,
        idempotency_key: str | None = None,
    ) -> DiagnosisReport:
        diagnoser = self._diagnosers.get(kind)
        if diagnoser is None:
            raise DiagnosisUnavailableError(f"diagnoser is not configured: {kind.value}")
        fingerprint = self._fingerprint(trace, kind, diagnoser.version_fingerprint)
        async with self._lock:
            if idempotency_key is not None:
                existing = self._idempotency_fingerprints.get(idempotency_key)
                if existing is not None and existing != fingerprint:
                    raise DiagnosisConflictError(
                        f"idempotency key conflict: {idempotency_key}"
                    )
                self._idempotency_fingerprints[idempotency_key] = fingerprint
            cached = self._completed.get(fingerprint)
            if cached is not None:
                return cached
            task = self._inflight.get(fingerprint)
            if task is None:
                task = asyncio.create_task(
                    self._execute_diagnosis(
                        trace,
                        kind,
                        diagnoser,
                        fingerprint=fingerprint,
                    )
                )
                task.add_done_callback(self._consume_task_exception)
                self._inflight[fingerprint] = task
        return await asyncio.shield(task)

    async def _execute_diagnosis(
        self,
        trace: TraceIR,
        kind: DiagnoserKind,
        diagnoser: Diagnoser,
        *,
        fingerprint: str,
    ) -> DiagnosisReport:
        try:
            context = TraceProjector().project(trace)
            evidence = EvidenceCatalog.from_context(context)
            execution = await diagnoser.diagnose(context, evidence)
            report = DiagnosisReport.from_execution(
                trace_id=trace.trace_id,
                run_id=trace.run_id,
                diagnoser=kind,
                execution=execution,
            )
        except BaseException:
            async with self._lock:
                if self._inflight.get(fingerprint) is asyncio.current_task():
                    self._inflight.pop(fingerprint, None)
            raise
        async with self._lock:
            self._completed[fingerprint] = report
            if self._inflight.get(fingerprint) is asyncio.current_task():
                self._inflight.pop(fingerprint, None)
            return report

    @staticmethod
    def _consume_task_exception(task: asyncio.Task[DiagnosisReport]) -> None:
        if not task.cancelled():
            task.exception()

    @staticmethod
    def _fingerprint(trace: TraceIR, kind: DiagnoserKind, version: str) -> str:
        trace_json = json.dumps(
            trace.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(f"{trace_json}\n{kind.value}\n{version}".encode()).hexdigest()
