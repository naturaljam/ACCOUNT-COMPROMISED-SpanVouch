from collections.abc import Callable

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
from spanvouch.contracts.trace import DiagnosticTraceView, SpanStatus
from spanvouch.contracts.verification import (
    FindingCode,
    ReviewInputSnapshot,
    VerificationInput,
    VerifierKind,
    VerifierVerdict,
)
from spanvouch.invariants.engine import InvariantEngine
from spanvouch.invariants.supportlab import supportlab_rules
from spanvouch.review.evidence_verifier import EvidenceVerifier
from spanvouch.review.models import (
    canonical_json,
    canonical_sha256,
)
from spanvouch.trace.evidence_catalog import EvidenceCatalog
from tests.review.factories import (
    make_diagnosis_report,
    make_review_snapshot,
    make_trace_view,
)


def _construct_report(report: DiagnosisReport, **updates: object) -> DiagnosisReport:
    values = {name: getattr(report, name) for name in DiagnosisReport.model_fields}
    values.update(updates)
    return DiagnosisReport.model_construct(**values)


def _construct_claim(claim: DiagnosisClaim, **updates: object) -> DiagnosisClaim:
    values = {name: getattr(claim, name) for name in DiagnosisClaim.model_fields}
    values.update(updates)
    return DiagnosisClaim.model_construct(**values)


def _construct_evidence(evidence: EvidenceRef, **updates: object) -> EvidenceRef:
    values = {name: getattr(evidence, name) for name in EvidenceRef.model_fields}
    values.update(updates)
    return EvidenceRef.model_construct(**values)


def _verification_input(
    report: DiagnosisReport,
    *,
    snapshot: ReviewInputSnapshot | None = None,
    report_sha256: str | None = None,
) -> VerificationInput:
    return VerificationInput.model_construct(
        snapshot=snapshot or make_review_snapshot(),
        report=report,
        report_sha256=report_sha256 or canonical_sha256(report),
    )


def _trace_mismatch(report: DiagnosisReport) -> VerificationInput:
    changed = _construct_report(report, trace_id="trace-forged")
    return _verification_input(changed)


def _run_mismatch(report: DiagnosisReport) -> VerificationInput:
    changed = _construct_report(report, run_id="run-forged")
    return _verification_input(changed)


def _stale_report_fingerprint(report: DiagnosisReport) -> VerificationInput:
    return _verification_input(report, report_sha256="0" * 64)


def _missing_provenance_version(report: DiagnosisReport) -> VerificationInput:
    provenance = DiagnosisProvenance(
        taxonomy=report.provenance.taxonomy,
        diagnoser_version=report.provenance.diagnoser_version,
        ruleset_version=None,
    )
    return _verification_input(_construct_report(report, provenance=provenance))


def _duplicate_critical_span(report: DiagnosisReport) -> VerificationInput:
    span_id = report.critical_span_ids[0]
    return _verification_input(
        _construct_report(report, critical_span_ids=(span_id, span_id))
    )


def _duplicate_evidence_id(report: DiagnosisReport) -> VerificationInput:
    first = report.evidence[0]
    resolved = EvidenceCatalog.from_view(make_trace_view()).resolve(
        EvidenceSelector(span_id="span-tool", field_path="status"),
        description="The refund tool ended in an error state.",
    )
    second = EvidenceRef(
        evidence_id=first.evidence_id,
        span_id=resolved.span_id,
        field_path=resolved.field_path,
        observed_value=resolved.observed_value,
        value_sha256=resolved.value_sha256,
        description=resolved.description,
    )
    assert second.canonical != first.canonical
    return _verification_input(_construct_report(report, evidence=(first, second)))


def _duplicate_selector(report: DiagnosisReport) -> VerificationInput:
    first = report.evidence[0]
    duplicate = _construct_evidence(first, evidence_id="ev-duplicate-selector")
    return _verification_input(_construct_report(report, evidence=(first, duplicate)))


def _duplicate_claim_reference(report: DiagnosisReport) -> VerificationInput:
    claim = report.causal_chain[0]
    evidence_id = claim.evidence_ids[0]
    duplicate = _construct_claim(claim, evidence_ids=(evidence_id, evidence_id))
    return _verification_input(_construct_report(report, causal_chain=(duplicate,)))


def _unknown_selector(report: DiagnosisReport) -> VerificationInput:
    evidence = _construct_evidence(
        report.evidence[0],
        field_path="attributes.tool.error.missing",
    )
    return _verification_input(_construct_report(report, evidence=(evidence,)))


def _different_observed_value(report: DiagnosisReport) -> VerificationInput:
    evidence = _construct_evidence(report.evidence[0], observed_value="ForgedValue")
    return _verification_input(_construct_report(report, evidence=(evidence,)))


def _different_value_sha256(report: DiagnosisReport) -> VerificationInput:
    evidence = _construct_evidence(report.evidence[0], value_sha256="f" * 64)
    return _verification_input(_construct_report(report, evidence=(evidence,)))


def _coordinated_evidence_id_rename(report: DiagnosisReport) -> VerificationInput:
    forged_id = "ev-coordinated-forgery"
    evidence = _construct_evidence(report.evidence[0], evidence_id=forged_id)
    claim = _construct_claim(report.causal_chain[0], evidence_ids=(forged_id,))
    return _verification_input(
        _construct_report(report, evidence=(evidence,), causal_chain=(claim,))
    )


def _unknown_claim_evidence(report: DiagnosisReport) -> VerificationInput:
    claim = _construct_claim(report.causal_chain[0], evidence_ids=("ev-unknown",))
    return _verification_input(_construct_report(report, causal_chain=(claim,)))


def _claim_without_evidence(report: DiagnosisReport) -> VerificationInput:
    claim = _construct_claim(report.causal_chain[0], evidence_ids=())
    return _verification_input(_construct_report(report, causal_chain=(claim,)))


def _critical_span_without_same_span_evidence(report: DiagnosisReport) -> VerificationInput:
    return _verification_input(
        _construct_report(
            report,
            critical_span_ids=(*report.critical_span_ids, "span-root"),
        )
    )


@pytest.fixture
def verifier() -> EvidenceVerifier:
    return EvidenceVerifier(InvariantEngine(()), policy_version="review-policy-v1")


@pytest.mark.parametrize(
    ("mutate", "expected_code", "revisable"),
    (
        (_trace_mismatch, FindingCode.INVALID_VERIFIER_OUTPUT, False),
        (_run_mismatch, FindingCode.INVALID_VERIFIER_OUTPUT, False),
        (_stale_report_fingerprint, FindingCode.INVALID_VERIFIER_OUTPUT, False),
        (_missing_provenance_version, FindingCode.INVALID_VERIFIER_OUTPUT, False),
        (_duplicate_critical_span, FindingCode.DUPLICATE_REFERENCE, False),
        (_duplicate_evidence_id, FindingCode.DUPLICATE_REFERENCE, False),
        (_duplicate_selector, FindingCode.DUPLICATE_REFERENCE, False),
        (_duplicate_claim_reference, FindingCode.DUPLICATE_REFERENCE, False),
        (_unknown_selector, FindingCode.INVALID_SELECTOR, True),
        (_different_observed_value, FindingCode.EVIDENCE_VALUE_MISMATCH, False),
        (_different_value_sha256, FindingCode.EVIDENCE_HASH_MISMATCH, False),
        (_coordinated_evidence_id_rename, FindingCode.INVALID_VERIFIER_OUTPUT, False),
        (_unknown_claim_evidence, FindingCode.CLAIM_NOT_GROUNDED, True),
        (_claim_without_evidence, FindingCode.CLAIM_NOT_GROUNDED, True),
        (
            _critical_span_without_same_span_evidence,
            FindingCode.CRITICAL_SPAN_NOT_GROUNDED,
            True,
        ),
    ),
)
async def test_integrity_defects_emit_exact_finding_code(
    verifier: EvidenceVerifier,
    mutate: Callable[[DiagnosisReport], VerificationInput],
    expected_code: FindingCode,
    revisable: bool,
) -> None:
    report = await verifier.verify(mutate(make_diagnosis_report()))

    assert tuple(finding.code for finding in report.findings) == (expected_code,)
    assert report.findings[0].revisable is revisable
    assert report.verdict is (
        VerifierVerdict.NEEDS_EVIDENCE if revisable else VerifierVerdict.REVIEW_REQUIRED
    )
    assert bool(report.evidence_gaps) is revisable


async def test_valid_report_is_verified_without_findings_or_gaps(
    verifier: EvidenceVerifier,
) -> None:
    input_ = _verification_input(make_diagnosis_report())

    report = await verifier.verify(input_)

    assert report.verifier_kind == VerifierKind.DETERMINISTIC
    assert report.verdict is VerifierVerdict.VERIFIED
    assert report.findings == ()
    assert report.evidence_gaps == ()


async def test_claim_referencing_only_noncritical_evidence_is_not_grounded(
    verifier: EvidenceVerifier,
) -> None:
    source = make_diagnosis_report()
    decoy = EvidenceCatalog.from_view(make_trace_view()).resolve(
        EvidenceSelector(span_id="span-root", field_path="status"),
        description="The root span ended in an error state.",
    )
    claim = DiagnosisClaim(
        stage=ClaimStage.CAUSE,
        statement=source.causal_chain[0].statement,
        evidence_ids=(decoy.evidence_id,),
    )
    changed = DiagnosisReport(
        **{
            **source.model_dump(exclude={"causal_chain", "evidence"}),
            "causal_chain": (claim,),
            "evidence": (*source.evidence, decoy),
        }
    )

    report = await verifier.verify(
        VerificationInput(
            snapshot=make_review_snapshot(),
            report=changed,
            report_sha256=canonical_sha256(changed),
        )
    )

    assert tuple(finding.code for finding in report.findings) == (
        FindingCode.CLAIM_NOT_GROUNDED,
    )
    assert report.verdict is VerifierVerdict.NEEDS_EVIDENCE
    assert len(report.evidence_gaps) == 1
    assert report.evidence_gaps[0].allowed_selectors
    assert report.evidence_gaps[0].related_span_ids == source.critical_span_ids


async def test_tampered_snapshot_hash_is_non_revisable_integrity_failure(
    verifier: EvidenceVerifier,
) -> None:
    snapshot = make_review_snapshot()
    tampered = ReviewInputSnapshot.model_construct(
        **{
            **{name: getattr(snapshot, name) for name in ReviewInputSnapshot.model_fields},
            "input_sha256": "0" * 64,
        }
    )

    report = await verifier.verify(
        _verification_input(make_diagnosis_report(), snapshot=tampered)
    )

    assert tuple(finding.code for finding in report.findings) == (
        FindingCode.INVALID_VERIFIER_OUTPUT,
    )
    assert report.verdict is VerifierVerdict.REVIEW_REQUIRED
    assert report.evidence_gaps == ()


async def test_duplicate_snapshot_span_ids_return_stable_integrity_failure(
    verifier: EvidenceVerifier,
) -> None:
    snapshot = make_review_snapshot()
    view = make_trace_view()
    duplicate_view = DiagnosticTraceView(spans=(view.spans[0], view.spans[0]))
    view_json = canonical_json(duplicate_view)
    duplicate_snapshot = ReviewInputSnapshot(
        trace_id=snapshot.trace_id,
        run_id=snapshot.run_id,
        view_json=view_json,
        input_sha256=canonical_sha256(view_json),
        catalog_version=snapshot.catalog_version,
        created_at=snapshot.created_at,
    )
    input_ = _verification_input(
        make_diagnosis_report(),
        snapshot=duplicate_snapshot,
    )

    first = await verifier.verify(input_)
    second = await verifier.verify(input_)

    assert first == second
    assert tuple(finding.code for finding in first.findings) == (
        FindingCode.INVALID_VERIFIER_OUTPUT,
    )
    assert first.findings[0].revisable is False
    assert first.verdict is VerifierVerdict.REVIEW_REQUIRED
    assert first.evidence_gaps == ()


async def test_revisable_gaps_only_offer_locally_rebuilt_selectors(
    verifier: EvidenceVerifier,
) -> None:
    report = await verifier.verify(_unknown_selector(make_diagnosis_report()))

    assert report.evidence_gaps
    assert report.evidence_gaps[0].allowed_selectors
    assert all(
        selector.startswith("span-tool::")
        for selector in report.evidence_gaps[0].allowed_selectors
    )
    assert "span-tool::attributes.tool.error.missing" not in (
        report.evidence_gaps[0].allowed_selectors
    )


async def test_finding_and_gap_identity_is_deterministic(
    verifier: EvidenceVerifier,
) -> None:
    input_ = _unknown_claim_evidence(make_diagnosis_report())

    first = await verifier.verify(input_)
    second = await verifier.verify(input_)

    assert first == second
    assert first.findings[0].finding_id == second.findings[0].finding_id
    assert first.evidence_gaps[0].gap_id == second.evidence_gaps[0].gap_id


async def test_all_applicable_findings_are_emitted_in_stable_order(
    verifier: EvidenceVerifier,
) -> None:
    report = make_diagnosis_report()
    unknown = _construct_evidence(
        report.evidence[0],
        field_path="attributes.tool.error.missing",
    )
    input_ = _verification_input(_construct_report(report, evidence=(unknown, unknown)))

    verified = await verifier.verify(input_)

    assert tuple(finding.code for finding in verified.findings) == (
        FindingCode.DUPLICATE_REFERENCE,
        FindingCode.INVALID_SELECTOR,
    )
    assert len(verified.evidence_gaps) == 1


async def test_unsupported_abstention_preserves_separate_supported_hard_conflict() -> None:
    view = make_trace_view()
    succeeded_root = view.spans[0].model_copy(
        update={"attributes": {"run.outcome": "succeeded"}}
    )
    unsupported_submit = view.spans[1].model_copy(
        update={
            "span_id": "span-unsupported-submit",
            "name": "submit_refund",
            "attributes": {"tool.error.message": "temporary_failure"},
        }
    )
    overlap_view = DiagnosticTraceView(
        spans=(succeeded_root, view.spans[1], unsupported_submit)
    )
    view_json = canonical_json(overlap_view)
    snapshot = ReviewInputSnapshot(
        trace_id="trace-review-1",
        run_id="run-review-1",
        view_json=view_json,
        input_sha256=canonical_sha256(view_json),
        catalog_version="evidence-catalog-v1",
        created_at=make_review_snapshot().created_at,
    )
    report = DiagnosisReport(
        trace_id=snapshot.trace_id,
        run_id=snapshot.run_id,
        diagnoser=DiagnoserKind.RULES,
        status=DiagnosisStatus.ABSTAINED,
        confidence=1.0,
        abstain_reason=AbstainReason.UNSUPPORTED_FAILURE_TYPE,
        provenance=DiagnosisProvenance(
            taxonomy=TaxonomyRef(taxonomy_id="supportlab", taxonomy_version="1.0"),
            diagnoser_version="overlap-rules-v1",
            ruleset_version="overlap-rules-v1",
        ),
    )
    verifier = EvidenceVerifier(
        InvariantEngine(supportlab_rules()),
        policy_version="review-policy-v1",
    )

    verified = await verifier.verify(_verification_input(report, snapshot=snapshot))

    assert tuple(finding.code for finding in verified.findings) == (
        FindingCode.DIAGNOSIS_CONFLICT,
    )
    assert verified.verdict is VerifierVerdict.REVIEW_REQUIRED


async def test_unsupported_abstention_preserves_same_span_different_invariant() -> None:
    view = make_trace_view()
    succeeded_root = view.spans[0].model_copy(
        update={"attributes": {"run.outcome": "succeeded"}}
    )
    policy_lookup = view.spans[1].model_copy(
        update={
            "span_id": "span-policy",
            "name": "get_refund_policy",
            "status": SpanStatus.OK,
            "attributes": {"tool.result": "manager_approval_required"},
        }
    )
    calculation = view.spans[1].model_copy(
        update={
            "span_id": "span-calculation",
            "name": "calculate_refund",
            "status": SpanStatus.OK,
            "attributes": {"tool.result": "10.00"},
        }
    )
    failed_submit = view.spans[1].model_copy(
        update={
            "span_id": "span-submit",
            "name": "submit_refund",
            "status": SpanStatus.ERROR,
            "attributes": {
                "tool.arguments.amount": "20.00",
                "tool.arguments.approval": "manager",
                "tool.error.message": "temporary_failure",
            },
        }
    )
    same_span_view = DiagnosticTraceView(
        spans=(succeeded_root, policy_lookup, calculation, failed_submit)
    )
    view_json = canonical_json(same_span_view)
    snapshot = ReviewInputSnapshot(
        trace_id="trace-review-1",
        run_id="run-review-1",
        view_json=view_json,
        input_sha256=canonical_sha256(view_json),
        catalog_version="evidence-catalog-v1",
        created_at=make_review_snapshot().created_at,
    )
    report = DiagnosisReport(
        trace_id=snapshot.trace_id,
        run_id=snapshot.run_id,
        diagnoser=DiagnoserKind.RULES,
        status=DiagnosisStatus.ABSTAINED,
        confidence=1.0,
        abstain_reason=AbstainReason.UNSUPPORTED_FAILURE_TYPE,
        provenance=DiagnosisProvenance(
            taxonomy=TaxonomyRef(taxonomy_id="supportlab", taxonomy_version="1.0"),
            diagnoser_version="same-span-rules-v1",
            ruleset_version="same-span-rules-v1",
        ),
    )
    verifier = EvidenceVerifier(
        InvariantEngine(supportlab_rules()),
        policy_version="review-policy-v1",
    )

    verified = await verifier.verify(_verification_input(report, snapshot=snapshot))

    assert tuple(finding.code for finding in verified.findings) == (
        FindingCode.DIAGNOSIS_CONFLICT,
    )
    assert verified.verdict is VerifierVerdict.REVIEW_REQUIRED
