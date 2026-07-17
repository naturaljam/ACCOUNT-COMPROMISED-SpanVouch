from typing import Protocol

from afc.diagnosis.evidence import EvidenceCatalog
from afc.diagnosis.models import DiagnosisExecution
from afc.diagnosis.trace_view import DiagnosticTraceView


class Diagnoser(Protocol):
    version_fingerprint: str

    async def diagnose(
        self, view: DiagnosticTraceView, evidence: EvidenceCatalog
    ) -> DiagnosisExecution:
        raise NotImplementedError
