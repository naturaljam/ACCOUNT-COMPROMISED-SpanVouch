from hashlib import sha256

from spanvouch.contracts.diagnosis import (
    AbstainReason,
    ClaimStage,
    DiagnosisClaim,
    DiagnosisDecision,
    DiagnosisExecution,
    DiagnosisProvenance,
    DiagnosisStatus,
    EvidenceRef,
    TaxonomyRef,
)
from spanvouch.contracts.trace import DiagnosticContext
from spanvouch.failure_types import SUPPORTED_DIAGNOSIS_FAILURE_TYPES, FailureType
from spanvouch.invariants.engine import InvariantEngine
from spanvouch.invariants.models import InvariantResult, InvariantStatus, RuleContext, RuleScope
from spanvouch.trace.evidence_catalog import EvidenceCatalog


def _unique_evidence(findings: tuple[InvariantResult, ...]) -> tuple[EvidenceRef, ...]:
    unique: dict[str, EvidenceRef] = {}
    for finding in findings:
        for item in finding.evidence:
            unique.setdefault(item.evidence_id, item)
    return tuple(unique.values())


class RuleDiagnoser:
    kind = "rules"

    def __init__(self, engine: InvariantEngine) -> None:
        self._engine = engine
        self.version_fingerprint = sha256(
            f"evidence-rules-v1:{engine.ruleset_version}".encode()
        ).hexdigest()

    async def diagnose(
        self, context: DiagnosticContext, evidence: EvidenceCatalog
    ) -> DiagnosisExecution:
        view = context.view
        results = self._engine.run(RuleContext(view=view, evidence=evidence))
        guards = tuple(
            result
            for result in results
            if result.scope is RuleScope.UNSUPPORTED_GUARD
            and result.status is InvariantStatus.FAILED
        )
        if guards:
            decision = DiagnosisDecision(
                status=DiagnosisStatus.ABSTAINED,
                evidence=_unique_evidence(guards),
                confidence=0.0,
                abstain_reason=AbstainReason.UNSUPPORTED_FAILURE_TYPE,
            )
            return self._execution(decision)

        supported = tuple(
            result
            for result in results
            if result.scope is RuleScope.SUPPORTED
            and result.status is InvariantStatus.FAILED
            and result.hard_failure
            and result.failure_type is not None
        )
        failure_types = {result.failure_type for result in supported}
        if len(failure_types) > 1:
            return self._execution(
                DiagnosisDecision(
                    status=DiagnosisStatus.ABSTAINED,
                    evidence=_unique_evidence(supported),
                    confidence=0.0,
                    abstain_reason=AbstainReason.AMBIGUOUS_FINDINGS,
                )
            )
        if len(failure_types) == 1:
            failure_type = next(iter(failure_types))
            assert failure_type is not None
            if failure_type not in SUPPORTED_DIAGNOSIS_FAILURE_TYPES:
                raise ValueError("SupportLab rule returned an unsupported failure type")
            matched = tuple(
                result for result in supported if result.failure_type is failure_type
            )
            resolved = _unique_evidence(matched)
            evidence_ids = tuple(item.evidence_id for item in resolved)
            critical_span_ids = tuple(dict.fromkeys(item.span_id for item in resolved))
            decision = DiagnosisDecision(
                status=DiagnosisStatus.DIAGNOSED,
                failure_type=failure_type,
                critical_span_ids=critical_span_ids,
                causal_chain=(
                    DiagnosisClaim(
                        stage=ClaimStage.CAUSE,
                        statement=matched[0].explanation,
                        evidence_ids=evidence_ids,
                    ),
                ),
                evidence=resolved,
                confidence=1.0,
            )
            return self._execution(decision)

        root = next(span for span in view.spans if span.parent_span_id is None)
        if root.attributes.get("run.outcome") == "succeeded":
            return self._execution(
                DiagnosisDecision(
                    status=DiagnosisStatus.NO_FAILURE,
                    failure_type=FailureType.NO_FAILURE,
                    confidence=1.0,
                )
            )
        return self._execution(
            DiagnosisDecision(
                status=DiagnosisStatus.ABSTAINED,
                confidence=0.0,
                abstain_reason=AbstainReason.INSUFFICIENT_EVIDENCE,
            )
        )

    def _execution(self, decision: DiagnosisDecision) -> DiagnosisExecution:
        return DiagnosisExecution(
            decision=decision,
            provenance=DiagnosisProvenance(
                taxonomy=TaxonomyRef(
                    taxonomy_id="supportlab", taxonomy_version="1.0"
                ),
                diagnoser_version="evidence-rules-v1",
                ruleset_version=self._engine.ruleset_version,
            ),
        )
