from __future__ import annotations

from collections.abc import Mapping

from spanvouch.diagnosis.models import DiagnoserKind, DiagnosisReport
from spanvouch.diagnosis.protocols import Diagnoser, RevisionCapableDiagnoser
from spanvouch.review.errors import ReviewConflictError
from spanvouch.review.models import EvidenceGap, ReviewRuntimeBundle
from spanvouch.trace.evidence_catalog import EvidenceCatalog


class DiagnosisReviser:
    """Adapt optional diagnoser revision to the persisted review runtime."""

    def __init__(self, diagnosers: Mapping[DiagnoserKind, Diagnoser]) -> None:
        self._diagnosers = dict(diagnosers)

    def supports(self, diagnoser_kind: DiagnoserKind) -> bool:
        diagnoser = self._diagnosers.get(diagnoser_kind)
        return isinstance(diagnoser, RevisionCapableDiagnoser)

    async def revise(
        self,
        runtime_bundle: ReviewRuntimeBundle,
        evidence_gaps: tuple[EvidenceGap, ...],
    ) -> DiagnosisReport:
        diagnoser_kind = runtime_bundle.case.diagnoser
        diagnoser = self._diagnosers.get(diagnoser_kind)
        if not isinstance(diagnoser, RevisionCapableDiagnoser):
            raise ReviewConflictError(
                "diagnoser does not support evidence revision"
            )

        if not runtime_bundle.revisions:
            raise ReviewConflictError(
                "review case has no current diagnosis revision"
            )
        current_revision = runtime_bundle.revisions[-1]
        if (
            current_revision.revision_number
            != runtime_bundle.case.current_revision_number
        ):
            raise ReviewConflictError(
                "current diagnosis revision conflicts with case"
            )
        previous_report = current_revision.report
        if (
            previous_report.trace_id != runtime_bundle.snapshot.trace_id
            or previous_report.trace_id != runtime_bundle.case.trace_id
        ):
            raise ReviewConflictError(
                "current diagnosis report has invalid trace binding"
            )
        if (
            previous_report.run_id != runtime_bundle.snapshot.run_id
            or previous_report.run_id != runtime_bundle.case.run_id
        ):
            raise ReviewConflictError(
                "current diagnosis report has invalid run binding"
            )
        if previous_report.diagnoser is not diagnoser_kind:
            raise ReviewConflictError(
                "current diagnosis report has invalid diagnoser binding"
            )

        view = runtime_bundle.snapshot.trace_view()
        evidence = EvidenceCatalog.from_view(view)
        execution = await diagnoser.revise(
            view,
            evidence,
            previous_report,
            evidence_gaps,
        )
        return DiagnosisReport(
            **execution.decision.model_dump(),
            trace_id=runtime_bundle.snapshot.trace_id,
            run_id=runtime_bundle.snapshot.run_id,
            diagnoser=diagnoser_kind,
            provenance=execution.provenance,
            usage=execution.usage,
        )
