import json
from collections.abc import Iterable
from hashlib import sha256

from pydantic import JsonValue, ValidationError

from afc.diagnosis.evidence import EvidenceCatalog
from afc.diagnosis.evidence import canonical_json as evidence_json
from afc.diagnosis.models import (
    AbstainReason,
    ClaimStage,
    DiagnoserKind,
    DiagnosisReport,
    DiagnosisStatus,
    EvidenceSelector,
)
from afc.diagnosis.trace_view import DiagnosticTraceView
from afc.failure_types import FailureType
from afc.invariants.engine import InvariantEngine
from afc.invariants.models import InvariantResult, InvariantStatus, RuleContext, RuleScope
from afc.review.models import (
    EvidenceGap,
    FindingCode,
    FindingSeverity,
    VerificationFinding,
    VerificationInput,
    VerifierKind,
    VerifierProvenance,
    VerifierReport,
    VerifierVerdict,
    canonical_json,
    canonical_sha256,
)

_FINDING_ORDER = {
    FindingCode.INVALID_VERIFIER_OUTPUT: 0,
    FindingCode.DUPLICATE_REFERENCE: 1,
    FindingCode.INVALID_SELECTOR: 2,
    FindingCode.EVIDENCE_VALUE_MISMATCH: 3,
    FindingCode.EVIDENCE_HASH_MISMATCH: 4,
    FindingCode.CLAIM_NOT_GROUNDED: 5,
    FindingCode.CRITICAL_SPAN_NOT_GROUNDED: 6,
    FindingCode.EVIDENCE_BUDGET_EXCEEDED: 7,
    FindingCode.CLEAN_TRACE_CONFLICT: 8,
    FindingCode.UNSUPPORTED_SCOPE: 9,
    FindingCode.DIAGNOSIS_CONFLICT: 10,
}

_MESSAGES = {
    FindingCode.INVALID_VERIFIER_OUTPUT: "The diagnosis report is not bound to its stored input.",
    FindingCode.DUPLICATE_REFERENCE: "The diagnosis report contains duplicate references.",
    FindingCode.INVALID_SELECTOR: "An evidence selector does not exist in the stored input.",
    FindingCode.EVIDENCE_VALUE_MISMATCH: (
        "An evidence value differs from the locally resolved value."
    ),
    FindingCode.EVIDENCE_HASH_MISMATCH: (
        "An evidence hash differs from the locally recomputed hash."
    ),
    FindingCode.CLAIM_NOT_GROUNDED: "A diagnosis claim is not grounded by report evidence.",
    FindingCode.CRITICAL_SPAN_NOT_GROUNDED: (
        "A critical span has no evidence from the same span."
    ),
    FindingCode.EVIDENCE_BUDGET_EXCEEDED: (
        "The diagnosis report exceeds the deterministic evidence budget."
    ),
    FindingCode.CLEAN_TRACE_CONFLICT: (
        "A diagnosed failure conflicts with the stored successful root outcome."
    ),
    FindingCode.UNSUPPORTED_SCOPE: (
        "An unsupported scope guard requires an unsupported-failure abstention."
    ),
    FindingCode.DIAGNOSIS_CONFLICT: (
        "The diagnosis type conflicts with a supported hard invariant."
    ),
}

_MAX_CLAIM_EVIDENCE = 4
_MAX_REPORT_EVIDENCE = 8


def _stable_id(
    prefix: str,
    *,
    policy_version: str,
    code: FindingCode,
    report_sha256: str,
    selectors: Iterable[str],
    discriminator: str = "",
) -> str:
    affected_selectors = tuple(sorted(set(selectors)))
    source = canonical_json(
        {
            "policy_version": policy_version,
            "code": code.value,
            "report_sha256": report_sha256,
            "affected_selectors": list(affected_selectors),
            "discriminator": discriminator,
        }
    )
    return f"{prefix}-{sha256(source.encode('utf-8')).hexdigest()}"


def _finding_id(
    *,
    policy_version: str,
    code: FindingCode,
    report_sha256: str,
    selectors: Iterable[str],
) -> str:
    return _stable_id(
        "finding",
        policy_version=policy_version,
        code=code,
        report_sha256=report_sha256,
        selectors=selectors,
    )


def _gap_id(
    *,
    policy_version: str,
    code: FindingCode,
    report_sha256: str,
    selectors: Iterable[str],
    discriminator: str,
) -> str:
    return _stable_id(
        "gap",
        policy_version=policy_version,
        code=code,
        report_sha256=report_sha256,
        selectors=selectors,
        discriminator=discriminator,
    )


def _selectors_for_spans(
    catalog: EvidenceCatalog,
    span_ids: Iterable[str],
) -> tuple[str, ...]:
    prefixes = tuple(f"{span_id}::" for span_id in sorted(set(span_ids)))
    return tuple(
        selector for selector in catalog.selectors if selector.startswith(prefixes)
    )


def _resolve_selector(
    catalog: EvidenceCatalog,
    *,
    span_id: str,
    field_path: str,
    description: str,
) -> tuple[str, JsonValue, str] | None:
    selector = EvidenceSelector(span_id=span_id, field_path=field_path)
    try:
        resolved = catalog.resolve(selector, description=description)
    except KeyError:
        return None
    recomputed_sha256 = sha256(
        evidence_json(resolved.observed_value).encode("utf-8")
    ).hexdigest()
    return resolved.evidence_id, resolved.observed_value, recomputed_sha256


def _rule_context(
    view: DiagnosticTraceView,
    catalog: EvidenceCatalog,
) -> RuleContext:
    return RuleContext(view=view, evidence=catalog)


def _result_selectors(results: Iterable[InvariantResult]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                evidence.canonical
                for result in results
                for evidence in result.evidence
            }
        )
    )


def _result_spans(results: Iterable[InvariantResult]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                evidence.span_id
                for result in results
                for evidence in result.evidence
            }
        )
    )


def _locally_known_selectors(
    catalog: EvidenceCatalog,
    selectors: Iterable[str],
) -> tuple[str, ...]:
    known = set(catalog.selectors)
    return tuple(sorted(set(selectors) & known))


def _ungrounded_critical_spans(report: DiagnosisReport) -> tuple[str, ...]:
    evidence_spans = {evidence.span_id for evidence in report.evidence}
    return tuple(
        sorted(
            span_id
            for span_id in set(report.critical_span_ids)
            if span_id not in evidence_spans
        )
    )


def _provenance_is_complete(report: DiagnosisReport) -> bool:
    provenance = report.provenance
    if not provenance.taxonomy_version or not provenance.diagnoser_version:
        return False
    if report.diagnoser is DiagnoserKind.RULES:
        return bool(provenance.ruleset_version)
    return bool(provenance.prompt_version and provenance.prompt_sha256)


def _has_duplicates(values: Iterable[str]) -> bool:
    materialized = tuple(values)
    return len(materialized) != len(set(materialized))


def _unique_gaps(gaps: Iterable[EvidenceGap]) -> tuple[EvidenceGap, ...]:
    by_id = {gap.gap_id: gap for gap in gaps}
    return tuple(sorted(by_id.values(), key=lambda gap: gap.gap_id))


class EvidenceVerifier:
    kind = VerifierKind.DETERMINISTIC

    def __init__(self, engine: InvariantEngine, *, policy_version: str) -> None:
        self._engine = engine
        self._policy_version = policy_version
        version_source = f"evidence-verifier-v1:{policy_version}:{engine.ruleset_version}"
        self.version_fingerprint = sha256(version_source.encode("utf-8")).hexdigest()

    async def verify(self, input_: VerificationInput) -> VerifierReport:
        report_hash = canonical_sha256(input_.report)
        findings: dict[FindingCode, tuple[bool, set[str], set[str]]] = {}
        gaps: list[EvidenceGap] = []

        def add_finding(
            code: FindingCode,
            *,
            revisable: bool,
            selectors: Iterable[str] = (),
            span_ids: Iterable[str] = (),
        ) -> None:
            current = findings.get(code)
            if current is None:
                findings[code] = (revisable, set(selectors), set(span_ids))
                return
            current_revisable, current_selectors, current_spans = current
            current_selectors.update(selectors)
            current_spans.update(span_ids)
            findings[code] = (
                current_revisable and revisable,
                current_selectors,
                current_spans,
            )

        binding_valid = True
        if (
            report_hash != input_.report_sha256
            or input_.report.trace_id != input_.snapshot.trace_id
            or input_.report.run_id != input_.snapshot.run_id
            or not _provenance_is_complete(input_.report)
        ):
            binding_valid = False
            add_finding(FindingCode.INVALID_VERIFIER_OUTPUT, revisable=False)

        view: DiagnosticTraceView | None = None
        try:
            parsed_view = json.loads(input_.snapshot.view_json)
            view = DiagnosticTraceView.model_validate(parsed_view)
            snapshot_hash = canonical_sha256(view)
            if (
                snapshot_hash != input_.snapshot.input_sha256
                or input_.snapshot.view_json != canonical_json(view)
            ):
                binding_valid = False
                view = None
                add_finding(FindingCode.INVALID_VERIFIER_OUTPUT, revisable=False)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
            binding_valid = False
            add_finding(FindingCode.INVALID_VERIFIER_OUTPUT, revisable=False)

        report = input_.report
        evidence_ids = tuple(evidence.evidence_id for evidence in report.evidence)
        selectors = tuple(evidence.canonical for evidence in report.evidence)
        duplicate_references = (
            _has_duplicates(report.critical_span_ids)
            or _has_duplicates(evidence_ids)
            or _has_duplicates(selectors)
            or any(_has_duplicates(claim.evidence_ids) for claim in report.causal_chain)
        )
        if duplicate_references:
            add_finding(
                FindingCode.DUPLICATE_REFERENCE,
                revisable=False,
                selectors=selectors,
                span_ids=report.critical_span_ids,
            )

        catalog: EvidenceCatalog | None = None
        if binding_valid and view is not None:
            try:
                catalog = EvidenceCatalog.from_view(view)
            except ValueError:
                binding_valid = False
                add_finding(FindingCode.INVALID_VERIFIER_OUTPUT, revisable=False)
        if catalog is not None and view is not None:
            for evidence in report.evidence:
                resolved = _resolve_selector(
                    catalog,
                    span_id=evidence.span_id,
                    field_path=evidence.field_path,
                    description=evidence.description,
                )
                if resolved is None:
                    add_finding(
                        FindingCode.INVALID_SELECTOR,
                        revisable=True,
                        selectors=(evidence.canonical,),
                        span_ids=(evidence.span_id,),
                    )
                    allowed = _selectors_for_spans(catalog, (evidence.span_id,))
                    gaps.append(
                        EvidenceGap(
                            gap_id=_gap_id(
                                policy_version=self._policy_version,
                                code=FindingCode.INVALID_SELECTOR,
                                report_sha256=report_hash,
                                selectors=allowed,
                                discriminator=evidence.canonical,
                            ),
                            finding_code=FindingCode.INVALID_SELECTOR,
                            required_evidence_kind="valid_selector",
                            allowed_selectors=allowed,
                            related_span_ids=(evidence.span_id,),
                            instruction="Select evidence from the stored diagnostic trace view.",
                        )
                    )
                    continue
                evidence_id, observed_value, value_hash = resolved
                if evidence.evidence_id != evidence_id and not duplicate_references:
                    add_finding(
                        FindingCode.INVALID_VERIFIER_OUTPUT,
                        revisable=False,
                        selectors=(evidence.canonical,),
                        span_ids=(evidence.span_id,),
                    )
                if evidence_json(evidence.observed_value) != evidence_json(observed_value):
                    add_finding(
                        FindingCode.EVIDENCE_VALUE_MISMATCH,
                        revisable=False,
                        selectors=(evidence.canonical,),
                        span_ids=(evidence.span_id,),
                    )
                if evidence.value_sha256 != value_hash:
                    add_finding(
                        FindingCode.EVIDENCE_HASH_MISMATCH,
                        revisable=False,
                        selectors=(evidence.canonical,),
                        span_ids=(evidence.span_id,),
                    )

            known_evidence = {evidence.evidence_id: evidence for evidence in report.evidence}
            if report.status is DiagnosisStatus.DIAGNOSED:
                critical_evidence_ids = {
                    evidence.evidence_id
                    for evidence in report.evidence
                    if evidence.span_id in report.critical_span_ids
                }
                for claim_index, claim in enumerate(report.causal_chain):
                    referenced = tuple(
                        known_evidence[evidence_id]
                        for evidence_id in claim.evidence_ids
                        if evidence_id in known_evidence
                    )
                    unknown_ids = set(claim.evidence_ids) - set(known_evidence)
                    if len(set(claim.evidence_ids)) > _MAX_CLAIM_EVIDENCE:
                        allowed = _locally_known_selectors(
                            catalog,
                            (evidence.canonical for evidence in referenced),
                        )
                        claim_spans = tuple(
                            sorted(
                                {
                                    evidence.span_id
                                    for evidence in referenced
                                    if evidence.canonical in allowed
                                }
                            )
                        )
                        add_finding(
                            FindingCode.EVIDENCE_BUDGET_EXCEEDED,
                            revisable=True,
                            selectors=allowed,
                            span_ids=claim_spans,
                        )
                        gaps.append(
                            EvidenceGap(
                                gap_id=_gap_id(
                                    policy_version=self._policy_version,
                                    code=FindingCode.EVIDENCE_BUDGET_EXCEEDED,
                                    report_sha256=report_hash,
                                    selectors=allowed,
                                    discriminator=f"claim:{claim_index}",
                                ),
                                finding_code=FindingCode.EVIDENCE_BUDGET_EXCEEDED,
                                claim_index=claim_index,
                                stage=claim.stage,
                                required_evidence_kind="claim_evidence_budget",
                                allowed_selectors=allowed,
                                related_span_ids=claim_spans,
                                instruction=(
                                    "Reduce this claim to at most four decisive evidence "
                                    "references."
                                ),
                            )
                        )
                    cause_is_off_critical_path = (
                        claim.stage is ClaimStage.CAUSE
                        and not set(claim.evidence_ids).intersection(
                            critical_evidence_ids
                        )
                    )
                    if (
                        not claim.evidence_ids
                        or unknown_ids
                        or cause_is_off_critical_path
                    ):
                        claim_spans = (
                            report.critical_span_ids
                            if cause_is_off_critical_path
                            else tuple(evidence.span_id for evidence in referenced)
                        )
                        if not claim_spans:
                            claim_spans = report.critical_span_ids
                        allowed = _selectors_for_spans(catalog, claim_spans)
                        related_selectors = tuple(
                            evidence.canonical for evidence in referenced
                        )
                        add_finding(
                            FindingCode.CLAIM_NOT_GROUNDED,
                            revisable=True,
                            selectors=related_selectors,
                            span_ids=claim_spans,
                        )
                        gaps.append(
                            EvidenceGap(
                                gap_id=_gap_id(
                                    policy_version=self._policy_version,
                                    code=FindingCode.CLAIM_NOT_GROUNDED,
                                    report_sha256=report_hash,
                                    selectors=allowed,
                                    discriminator=f"claim:{claim_index}",
                                ),
                                finding_code=FindingCode.CLAIM_NOT_GROUNDED,
                                claim_index=claim_index,
                                stage=claim.stage,
                                required_evidence_kind="claim_grounding",
                                allowed_selectors=allowed,
                                related_span_ids=tuple(sorted(set(claim_spans))),
                                instruction="Ground the claim with evidence from the stored input.",
                            )
                        )

                for span_id in _ungrounded_critical_spans(report):
                    allowed = _selectors_for_spans(catalog, (span_id,))
                    add_finding(
                        FindingCode.CRITICAL_SPAN_NOT_GROUNDED,
                        revisable=True,
                        span_ids=(span_id,),
                    )
                    gaps.append(
                        EvidenceGap(
                            gap_id=_gap_id(
                                policy_version=self._policy_version,
                                code=FindingCode.CRITICAL_SPAN_NOT_GROUNDED,
                                report_sha256=report_hash,
                                selectors=allowed,
                                discriminator=f"span:{span_id}",
                            ),
                            finding_code=FindingCode.CRITICAL_SPAN_NOT_GROUNDED,
                            required_evidence_kind="critical_span_grounding",
                            allowed_selectors=allowed,
                            related_span_ids=(span_id,),
                            instruction="Add evidence resolved from the critical span.",
                        )
                    )

            unique_report_evidence = {
                evidence.evidence_id: evidence for evidence in report.evidence
            }
            if len(unique_report_evidence) > _MAX_REPORT_EVIDENCE:
                allowed = _locally_known_selectors(
                    catalog,
                    (evidence.canonical for evidence in unique_report_evidence.values()),
                )
                report_spans = tuple(
                    sorted(
                        {
                            evidence.span_id
                            for evidence in unique_report_evidence.values()
                            if evidence.canonical in allowed
                        }
                    )
                )
                add_finding(
                    FindingCode.EVIDENCE_BUDGET_EXCEEDED,
                    revisable=True,
                    selectors=allowed,
                    span_ids=report_spans,
                )
                gaps.append(
                    EvidenceGap(
                        gap_id=_gap_id(
                            policy_version=self._policy_version,
                            code=FindingCode.EVIDENCE_BUDGET_EXCEEDED,
                            report_sha256=report_hash,
                            selectors=allowed,
                            discriminator="report",
                        ),
                        finding_code=FindingCode.EVIDENCE_BUDGET_EXCEEDED,
                        required_evidence_kind="report_evidence_budget",
                        allowed_selectors=allowed,
                        related_span_ids=report_spans,
                        instruction=(
                            "Reduce the report to at most eight decisive evidence references."
                        ),
                    )
                )

            invariant_results = self._engine.run(_rule_context(view, catalog))
            unsupported_failures = tuple(
                result
                for result in invariant_results
                if result.scope is RuleScope.UNSUPPORTED_GUARD
                and result.status is InvariantStatus.FAILED
                and result.hard_failure
            )
            accepted_unsupported = (
                report.status is DiagnosisStatus.ABSTAINED
                and report.abstain_reason is AbstainReason.UNSUPPORTED_FAILURE_TYPE
            )
            if unsupported_failures and not accepted_unsupported:
                add_finding(
                    FindingCode.UNSUPPORTED_SCOPE,
                    revisable=False,
                    selectors=_result_selectors(unsupported_failures),
                    span_ids=_result_spans(unsupported_failures),
                )

            supported_failures = tuple(
                result
                for result in invariant_results
                if result.scope is RuleScope.SUPPORTED
                and result.status is InvariantStatus.FAILED
                and result.hard_failure
                and result.failure_type is not None
            )
            if accepted_unsupported:
                unsupported_span_ids = set(_result_spans(unsupported_failures))
                supported_failures = tuple(
                    result
                    for result in supported_failures
                    if not set(_result_spans((result,))).intersection(
                        unsupported_span_ids
                    )
                )
            root = next(
                (span for span in view.spans if span.parent_span_id is None),
                None,
            )
            if (
                root is not None
                and root.attributes.get("run.outcome") == "succeeded"
                and report.status is DiagnosisStatus.DIAGNOSED
                and not supported_failures
                and not unsupported_failures
            ):
                add_finding(
                    FindingCode.CLEAN_TRACE_CONFLICT,
                    revisable=False,
                    selectors=_locally_known_selectors(
                        catalog,
                        (f"{root.span_id}::attributes.run.outcome",),
                    ),
                    span_ids=(root.span_id,),
                )

            conflicts: tuple[InvariantResult, ...]
            if report.status is DiagnosisStatus.DIAGNOSED:
                conflicts = tuple(
                    result
                    for result in supported_failures
                    if result.failure_type is not report.failure_type
                )
            else:
                conflicts = supported_failures
            if conflicts:
                add_finding(
                    FindingCode.DIAGNOSIS_CONFLICT,
                    revisable=False,
                    selectors=_result_selectors(conflicts),
                    span_ids=_result_spans(conflicts),
                )

            if report.status is DiagnosisStatus.DIAGNOSED:
                loop_failures = tuple(
                    result
                    for result in supported_failures
                    if result.failure_type is FailureType.LOOP_OR_BUDGET_EXHAUSTION
                    and result.evidence
                )
                if (
                    report.failure_type is FailureType.LOOP_OR_BUDGET_EXHAUSTION
                    and loop_failures
                ):
                    expected_span = loop_failures[0].evidence[0].span_id
                    reported_span = report.critical_span_ids[0]
                    if reported_span != expected_span:
                        allowed = _selectors_for_spans(catalog, (expected_span,))
                        add_finding(
                            FindingCode.CRITICAL_SPAN_NOT_GROUNDED,
                            revisable=True,
                            selectors=allowed,
                            span_ids=(expected_span,),
                        )
                        gaps.append(
                            EvidenceGap(
                                gap_id=_gap_id(
                                    policy_version=self._policy_version,
                                    code=FindingCode.CRITICAL_SPAN_NOT_GROUNDED,
                                    report_sha256=report_hash,
                                    selectors=allowed,
                                    discriminator=(
                                        f"loop-span:{reported_span}:{expected_span}"
                                    ),
                                ),
                                finding_code=FindingCode.CRITICAL_SPAN_NOT_GROUNDED,
                                required_evidence_kind="loop_critical_span_grounding",
                                allowed_selectors=allowed,
                                related_span_ids=(expected_span,),
                                instruction=(
                                    "Ground the loop diagnosis in the deterministic last "
                                    "repeated span."
                                ),
                            )
                        )

        ordered_findings = tuple(
            VerificationFinding(
                finding_id=_finding_id(
                    policy_version=self._policy_version,
                    code=code,
                    report_sha256=report_hash,
                    selectors=related_selectors,
                ),
                code=code,
                severity=FindingSeverity.HARD,
                message=_MESSAGES[code],
                revisable=revisable,
                related_selectors=tuple(sorted(related_selectors)),
                related_span_ids=tuple(sorted(related_spans)),
            )
            for code, (revisable, related_selectors, related_spans) in sorted(
                findings.items(), key=lambda item: _FINDING_ORDER[item[0]]
            )
        )
        ordered_gaps = _unique_gaps(gaps)
        if not ordered_findings:
            verdict = VerifierVerdict.VERIFIED
        elif all(finding.revisable for finding in ordered_findings):
            verdict = VerifierVerdict.NEEDS_EVIDENCE
        else:
            verdict = VerifierVerdict.REVIEW_REQUIRED

        run_source = (
            f"{self.version_fingerprint}:{report_hash}:{input_.snapshot.input_sha256}:"
            f"{input_.revision_number}"
        )
        verifier_run_id = f"verifier-{sha256(run_source.encode('utf-8')).hexdigest()}"
        return VerifierReport(
            verifier_run_id=verifier_run_id,
            revision_number=input_.revision_number,
            report_sha256=report_hash,
            verifier_kind=self.kind,
            verdict=verdict,
            findings=ordered_findings,
            evidence_gaps=ordered_gaps,
            provenance=VerifierProvenance(
                verifier_kind=self.kind,
                verifier_version=self.version_fingerprint,
                policy_version=self._policy_version,
            ),
            started_at=input_.snapshot.created_at,
            completed_at=input_.snapshot.created_at,
        )
