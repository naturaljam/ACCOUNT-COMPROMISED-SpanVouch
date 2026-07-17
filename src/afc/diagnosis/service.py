import asyncio
import json
from collections.abc import Mapping
from hashlib import sha256

from afc.diagnosis.errors import DiagnosisConflictError, DiagnosisUnavailableError
from afc.diagnosis.evidence import EvidenceCatalog
from afc.diagnosis.models import DiagnoserKind, DiagnosisReport
from afc.diagnosis.protocols import Diagnoser
from afc.diagnosis.trace_view import DiagnosticTraceView
from afc.trace_ir.models import TraceIR


class DiagnosisService:
    def __init__(self, diagnosers: Mapping[DiagnoserKind, Diagnoser]) -> None:
        self._diagnosers = dict(diagnosers)
        self._completed: dict[str, DiagnosisReport] = {}
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

            view = DiagnosticTraceView.from_trace(trace)
            evidence = EvidenceCatalog.from_view(view)
            execution = await diagnoser.diagnose(view, evidence)
            report = DiagnosisReport(
                **execution.decision.model_dump(),
                trace_id=trace.trace_id,
                run_id=trace.run_id,
                diagnoser=kind,
                provenance=execution.provenance,
                usage=execution.usage,
            )
            self._completed[fingerprint] = report
            return report

    @staticmethod
    def _fingerprint(trace: TraceIR, kind: DiagnoserKind, version: str) -> str:
        trace_json = json.dumps(
            trace.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(f"{trace_json}\n{kind.value}\n{version}".encode()).hexdigest()
