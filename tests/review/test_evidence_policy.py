from datetime import UTC, datetime, timedelta

import pytest

from spanvouch.contracts.diagnosis import (
    AbstainReason,
    ClaimStage,
    DiagnoserKind,
    DiagnosisClaim,
    DiagnosisProvenance,
    DiagnosisReport,
    DiagnosisStatus,
    EvidenceRef,
    EvidenceSelector,
    TaxonomyRef,
)
from spanvouch.contracts.trace import (
    DiagnosticContext,
    DiagnosticSpan,
    DiagnosticTraceView,
    SpanKind,
    SpanStatus,
)
from spanvouch.contracts.verification import (
    FindingCode,
    ReviewInputSnapshot,
    VerificationInput,
    VerifierVerdict,
)
from spanvouch.contracts.versioning import (
    canonical_json,
    canonical_sha256,
)
from spanvouch.diagnosis.rule_diagnoser import RuleDiagnoser
from spanvouch.failure_types import FailureType
from spanvouch.invariants.supportlab import supportlab_rules
from spanvouch.trace.evidence_catalog import EvidenceCatalog
from spanvouch.verification.deterministic import DeterministicVerifier
from spanvouch.verification.invariant_engine import InvariantEngine
from tests.trace.test_diagnostic_view import load_trace, project_trace

NOW = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


def _span(
    span_id: str,
    *,
    name: str,
    kind: SpanKind,
    status: SpanStatus,
    offset: int,
    parent_span_id: str | None = None,
    attributes: dict[str, object] | None = None,
) -> DiagnosticSpan:
    return DiagnosticSpan(
        span_id=span_id,
        parent_span_id=parent_span_id,
        name=name,
        kind=kind,
        status=status,
        started_at=NOW + timedelta(seconds=offset),
        ended_at=NOW + timedelta(seconds=offset + 1),
        attributes=attributes or {},
    )


def _view(
    *,
    outcome: str,
    tools: tuple[DiagnosticSpan, ...],
    root_status: SpanStatus = SpanStatus.ERROR,
) -> DiagnosticTraceView:
    root = _span(
        "span-root",
        name="support-agent",
        kind=SpanKind.AGENT,
        status=root_status,
        offset=0,
        attributes={"run.outcome": outcome},
    )
    return DiagnosticTraceView(spans=(root, *tools))


def _known_tool(
    span_id: str = "span-tool",
    *,
    status: SpanStatus = SpanStatus.OK,
    offset: int = 1,
) -> DiagnosticSpan:
    return _span(
        span_id,
        name="get_customer",
        kind=SpanKind.TOOL,
        status=status,
        offset=offset,
        parent_span_id="span-root",
        attributes={"tool.name": "get_customer", "tool.result": {"customer_id": "c-1"}},
    )


def _snapshot(
    view: DiagnosticTraceView,
    *,
    trace_id: str = "trace-policy-1",
    run_id: str = "run-policy-1",
) -> ReviewInputSnapshot:
    view_json = canonical_json(view)
    return ReviewInputSnapshot(
        trace_id=trace_id,
        run_id=run_id,
        view_json=view_json,
        input_sha256=canonical_sha256(view_json),
        catalog_version="evidence-catalog-v1",
        created_at=NOW,
    )


def _resolve(view: DiagnosticTraceView, selectors: tuple[str, ...]) -> tuple[EvidenceRef, ...]:
    catalog = EvidenceCatalog.from_view(view)
    resolved: list[EvidenceRef] = []
    for index, canonical in enumerate(selectors):
        span_id, field_path = canonical.split("::", maxsplit=1)
        resolved.append(
            catalog.resolve(
                EvidenceSelector(span_id=span_id, field_path=field_path),
                description=f"Stored diagnostic field {index}.",
            )
        )
    return tuple(resolved)


def _diagnosed_report(
    view: DiagnosticTraceView,
    *,
    failure_type: FailureType,
    critical_span_ids: tuple[str, ...],
    selectors: tuple[str, ...],
    claim_groups: tuple[tuple[int, ...], ...],
) -> DiagnosisReport:
    evidence = _resolve(view, selectors)
    return DiagnosisReport(
        trace_id="trace-policy-1",
        run_id="run-policy-1",
        diagnoser=DiagnoserKind.RULES,
        status=DiagnosisStatus.DIAGNOSED,
        failure_type=failure_type,
        critical_span_ids=critical_span_ids,
        causal_chain=tuple(
            DiagnosisClaim(
                stage=(
                    ClaimStage.CAUSE,
                    ClaimStage.PROPAGATION,
                    ClaimStage.OUTCOME,
                )[claim_index],
                statement=f"Diagnosis claim {claim_index}.",
                evidence_ids=tuple(evidence[index].evidence_id for index in indexes),
            )
            for claim_index, indexes in enumerate(claim_groups)
        ),
        evidence=evidence,
        confidence=1.0,
        provenance=DiagnosisProvenance(
            taxonomy=TaxonomyRef(taxonomy_id="supportlab", taxonomy_version="1.0"),
            diagnoser_version="review-test-rules-v1",
            ruleset_version="review-test-rules-v1",
        ),
    )


def _no_failure_report() -> DiagnosisReport:
    return DiagnosisReport(
        trace_id="trace-policy-1",
        run_id="run-policy-1",
        diagnoser=DiagnoserKind.RULES,
        status=DiagnosisStatus.NO_FAILURE,
        failure_type=FailureType.NO_FAILURE,
        confidence=1.0,
        provenance=DiagnosisProvenance(
            taxonomy=TaxonomyRef(taxonomy_id="supportlab", taxonomy_version="1.0"),
            diagnoser_version="review-test-rules-v1",
            ruleset_version="review-test-rules-v1",
        ),
    )


def _input(view: DiagnosticTraceView, report: DiagnosisReport) -> VerificationInput:
    return VerificationInput(
        snapshot=_snapshot(view, trace_id=report.trace_id, run_id=report.run_id),
        report=report,
        report_sha256=canonical_sha256(report),
    )


def _verifier() -> DeterministicVerifier:
    return DeterministicVerifier(
        InvariantEngine(supportlab_rules()),
        policy_version="review-policy-v1",
    )


async def _rules_report(run_id: str) -> tuple[DiagnosticTraceView, DiagnosisReport]:
    trace = load_trace(run_id)
    view = project_trace(trace)
    context = DiagnosticContext(trace_id=trace.trace_id, run_id=trace.run_id, view=view)
    catalog = EvidenceCatalog.from_context(context)
    execution = await RuleDiagnoser(InvariantEngine(supportlab_rules())).diagnose(
        context,
        catalog,
    )
    report = DiagnosisReport.from_execution(
        trace_id=trace.trace_id,
        run_id=trace.run_id,
        diagnoser=DiagnoserKind.RULES,
        execution=execution,
    )
    return view, report


async def test_five_unique_references_on_one_claim_exceed_claim_budget() -> None:
    view = _view(outcome="failed", tools=(_known_tool(),))
    selectors = tuple(
        selector
        for selector in EvidenceCatalog.from_view(view).selectors
        if selector.startswith("span-tool::")
    )[:5]
    diagnosis = _diagnosed_report(
        view,
        failure_type=FailureType.POLICY_VIOLATION,
        critical_span_ids=("span-tool",),
        selectors=selectors,
        claim_groups=((0, 1, 2, 3, 4),),
    )

    report = await _verifier().verify(_input(view, diagnosis))

    assert tuple(finding.code for finding in report.findings) == (
        FindingCode.EVIDENCE_BUDGET_EXCEEDED,
    )
    assert report.findings[0].revisable is True
    assert report.verdict is VerifierVerdict.NEEDS_EVIDENCE
    assert len(report.evidence_gaps) == 1
    assert report.evidence_gaps[0].claim_index == 0
    assert report.evidence_gaps[0].allowed_selectors == tuple(sorted(selectors))


async def test_nine_unique_references_exceed_report_budget() -> None:
    view = _view(outcome="failed", tools=(_known_tool(),))
    catalog = EvidenceCatalog.from_view(view)
    root_selectors = tuple(
        selector for selector in catalog.selectors if selector.startswith("span-root::")
    )[:4]
    tool_selectors = tuple(
        selector for selector in catalog.selectors if selector.startswith("span-tool::")
    )[:5]
    selectors = root_selectors + tool_selectors
    diagnosis = _diagnosed_report(
        view,
        failure_type=FailureType.POLICY_VIOLATION,
        critical_span_ids=("span-tool",),
        selectors=selectors,
        claim_groups=((0, 1, 4), (2, 3, 5), (6, 7, 8)),
    )

    report = await _verifier().verify(_input(view, diagnosis))

    assert tuple(finding.code for finding in report.findings) == (
        FindingCode.EVIDENCE_BUDGET_EXCEEDED,
    )
    assert report.verdict is VerifierVerdict.NEEDS_EVIDENCE
    assert len(report.evidence_gaps) == 1
    assert report.evidence_gaps[0].claim_index is None
    assert report.evidence_gaps[0].allowed_selectors == tuple(sorted(selectors))


async def test_clean_root_conflicts_with_diagnosed_failure() -> None:
    view = _view(
        outcome="succeeded",
        root_status=SpanStatus.OK,
        tools=(_known_tool(),),
    )
    diagnosis = _diagnosed_report(
        view,
        failure_type=FailureType.POLICY_VIOLATION,
        critical_span_ids=("span-tool",),
        selectors=("span-tool::name",),
        claim_groups=((0,),),
    )

    report = await _verifier().verify(_input(view, diagnosis))

    assert tuple(finding.code for finding in report.findings) == (
        FindingCode.CLEAN_TRACE_CONFLICT,
    )
    assert report.findings[0].revisable is False
    assert report.verdict is VerifierVerdict.REVIEW_REQUIRED
    assert report.evidence_gaps == ()


@pytest.mark.parametrize("run_id", ("invalid_final_state-01", "invalid_final_state-02"))
async def test_successful_root_with_supported_failure_is_not_deterministically_clean(
    run_id: str,
) -> None:
    view, diagnosis = await _rules_report(run_id)

    report = await _verifier().verify(_input(view, diagnosis))

    assert diagnosis.failure_type == FailureType.INVALID_FINAL_STATE
    assert report.verdict is VerifierVerdict.VERIFIED
    assert FindingCode.CLEAN_TRACE_CONFLICT not in {
        finding.code for finding in report.findings
    }


async def test_unsupported_guard_requires_unsupported_abstention() -> None:
    view = _view(
        outcome="succeeded",
        root_status=SpanStatus.OK,
        tools=(_known_tool(status=SpanStatus.ERROR),),
    )

    report = await _verifier().verify(_input(view, _no_failure_report()))

    assert tuple(finding.code for finding in report.findings) == (
        FindingCode.UNSUPPORTED_SCOPE,
    )
    assert report.findings[0].revisable is False
    assert report.verdict is VerifierVerdict.REVIEW_REQUIRED
    assert report.evidence_gaps == ()


async def test_supported_invariant_type_conflicts_with_diagnosis_type() -> None:
    unknown_tool = _span(
        "span-tool",
        name="unknown_tool",
        kind=SpanKind.TOOL,
        status=SpanStatus.OK,
        offset=1,
        parent_span_id="span-root",
    )
    view = _view(outcome="failed", tools=(unknown_tool,))
    diagnosis = _diagnosed_report(
        view,
        failure_type=FailureType.POLICY_VIOLATION,
        critical_span_ids=("span-tool",),
        selectors=("span-tool::name",),
        claim_groups=((0,),),
    )

    report = await _verifier().verify(_input(view, diagnosis))

    assert tuple(finding.code for finding in report.findings) == (
        FindingCode.DIAGNOSIS_CONFLICT,
    )
    assert report.findings[0].revisable is False
    assert report.verdict is VerifierVerdict.REVIEW_REQUIRED
    assert report.evidence_gaps == ()


async def test_no_failure_conflicts_with_supported_hard_failure() -> None:
    trace = load_trace("wrong_tool-01")
    view = project_trace(trace)
    diagnosis = DiagnosisReport(
        trace_id=trace.trace_id,
        run_id=trace.run_id,
        diagnoser=DiagnoserKind.RULES,
        status=DiagnosisStatus.NO_FAILURE,
        failure_type=FailureType.NO_FAILURE,
        confidence=1.0,
        provenance=DiagnosisProvenance(
            taxonomy=TaxonomyRef(taxonomy_id="supportlab", taxonomy_version="1.0"),
            diagnoser_version="review-test-rules-v1",
            ruleset_version="review-test-rules-v1",
        ),
    )

    report = await _verifier().verify(_input(view, diagnosis))

    assert tuple(finding.code for finding in report.findings) == (
        FindingCode.DIAGNOSIS_CONFLICT,
    )
    assert report.findings[0].revisable is False
    assert report.verdict is VerifierVerdict.REVIEW_REQUIRED
    assert report.evidence_gaps == ()


async def test_abstention_conflicts_with_supported_hard_failure() -> None:
    trace = load_trace("wrong_tool-01")
    view = project_trace(trace)
    diagnosis = DiagnosisReport(
        trace_id=trace.trace_id,
        run_id=trace.run_id,
        diagnoser=DiagnoserKind.RULES,
        status=DiagnosisStatus.ABSTAINED,
        confidence=0.0,
        abstain_reason=AbstainReason.INSUFFICIENT_EVIDENCE,
        provenance=DiagnosisProvenance(
            taxonomy=TaxonomyRef(taxonomy_id="supportlab", taxonomy_version="1.0"),
            diagnoser_version="review-test-rules-v1",
            ruleset_version="review-test-rules-v1",
        ),
    )

    report = await _verifier().verify(_input(view, diagnosis))

    assert tuple(finding.code for finding in report.findings) == (
        FindingCode.DIAGNOSIS_CONFLICT,
    )
    assert report.findings[0].revisable is False
    assert report.verdict is VerifierVerdict.REVIEW_REQUIRED
    assert report.evidence_gaps == ()


async def test_loop_critical_span_must_be_last_repeated_span() -> None:
    view = _view(
        outcome="step_limit",
        tools=(
            _known_tool("span-repeat-1", offset=1),
            _known_tool("span-repeat-2", offset=2),
        ),
    )
    diagnosis = _diagnosed_report(
        view,
        failure_type=FailureType.LOOP_OR_BUDGET_EXHAUSTION,
        critical_span_ids=("span-repeat-1",),
        selectors=("span-repeat-1::name",),
        claim_groups=((0,),),
    )

    report = await _verifier().verify(_input(view, diagnosis))

    assert tuple(finding.code for finding in report.findings) == (
        FindingCode.CRITICAL_SPAN_NOT_GROUNDED,
    )
    assert report.findings[0].revisable is True
    assert report.verdict is VerifierVerdict.NEEDS_EVIDENCE
    assert len(report.evidence_gaps) == 1
    assert report.evidence_gaps[0].related_span_ids == ("span-repeat-2",)
    assert report.evidence_gaps[0].allowed_selectors
    assert all(
        selector.startswith("span-repeat-2::")
        for selector in report.evidence_gaps[0].allowed_selectors
    )


async def test_deterministic_findings_do_not_count_toward_report_budget() -> None:
    unknown_tool = _span(
        "span-tool",
        name="unknown_tool",
        kind=SpanKind.TOOL,
        status=SpanStatus.OK,
        offset=1,
        parent_span_id="span-root",
        attributes={"tool.name": "unknown_tool", "tool.result": "unused"},
    )
    view = _view(outcome="failed", tools=(unknown_tool,))
    catalog = EvidenceCatalog.from_view(view)
    tool_selectors = tuple(
        selector
        for selector in catalog.selectors
        if selector.startswith("span-tool::")
        and not selector.endswith("::name")
        and not selector.endswith("::status")
    )[:4]
    root_selectors = tuple(
        selector for selector in catalog.selectors if selector.startswith("span-root::")
    )[:4]
    diagnosis = _diagnosed_report(
        view,
        failure_type=FailureType.WRONG_TOOL,
        critical_span_ids=("span-tool",),
        selectors=tool_selectors + root_selectors,
        claim_groups=((0, 1, 2, 3), (4, 5, 6, 7)),
    )

    report = await _verifier().verify(_input(view, diagnosis))

    assert len(diagnosis.evidence) == 8
    assert report.verdict is VerifierVerdict.VERIFIED
    assert FindingCode.EVIDENCE_BUDGET_EXCEEDED not in {
        finding.code for finding in report.findings
    }


async def test_supported_unsupported_abstention_is_accepted() -> None:
    view = _view(
        outcome="succeeded",
        root_status=SpanStatus.OK,
        tools=(_known_tool(status=SpanStatus.ERROR),),
    )
    diagnosis = DiagnosisReport(
        trace_id="trace-policy-1",
        run_id="run-policy-1",
        diagnoser=DiagnoserKind.RULES,
        status=DiagnosisStatus.ABSTAINED,
        confidence=0.0,
        abstain_reason=AbstainReason.UNSUPPORTED_FAILURE_TYPE,
        provenance=DiagnosisProvenance(
            taxonomy=TaxonomyRef(taxonomy_id="supportlab", taxonomy_version="1.0"),
            diagnoser_version="review-test-rules-v1",
            ruleset_version="review-test-rules-v1",
        ),
    )

    report = await _verifier().verify(_input(view, diagnosis))

    assert report.verdict is VerifierVerdict.VERIFIED
    assert report.findings == ()
