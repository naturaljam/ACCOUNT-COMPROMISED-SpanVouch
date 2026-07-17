from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from afc.diagnosis.trace_view import DiagnosticTraceView
from afc.evals.generate_review_dataset import MutationKind, ReviewCandidate
from afc.evals.review_labels import (
    ReviewGoldLabel,
    validate_review_cohort,
)
from afc.review.models import (
    FindingCode,
    ReviewInputSnapshot,
    VerificationInput,
    VerifierReport,
    VerifierVerdict,
    canonical_json,
    canonical_sha256,
)
from afc.trace_ir.models import TraceIR

_SNAPSHOT_TIME = datetime(2026, 7, 17, tzinfo=UTC)


class ReviewVerifier(Protocol):
    version_fingerprint: str

    async def verify(self, input_: VerificationInput) -> VerifierReport: ...


class ReviewMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    valid_report_pass_rate: float
    hard_defect_recall: float
    false_block_rate: float
    unsupported_scope_detection_rate: float
    claim_grounding_detection_rate: float
    critical_grounding_detection_rate: float
    evidence_gap_precision: float
    structured_output_success_rate: float
    operational_error_rate: float


class ReviewSampleResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    source_run_id: str
    mutation_kind: MutationKind
    verifier_report: VerifierReport | None = None
    operational_error: str | None = None


class ReviewUsageSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_sample_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_p50_ms: float | None = Field(default=None, ge=0.0)
    latency_p95_ms: float | None = Field(default=None, ge=0.0)
    estimated_cost_usd: float | None = Field(default=None, ge=0.0)


class ReviewEvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["complete", "partial"]
    candidate_count: int = Field(ge=1)
    samples: tuple[ReviewSampleResult, ...]
    metrics: ReviewMetrics
    verifier_version: str = Field(min_length=1)
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    usage: ReviewUsageSummary


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _percentile(values: tuple[float, ...], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _usage(samples: tuple[ReviewSampleResult, ...]) -> ReviewUsageSummary:
    usages = tuple(
        sample.verifier_report.usage
        for sample in samples
        if sample.verifier_report is not None and sample.verifier_report.usage is not None
    )
    latencies = tuple(usage.latency_ms for usage in usages)
    return ReviewUsageSummary(
        provider_sample_count=len(usages),
        input_tokens=sum(usage.input_tokens for usage in usages),
        output_tokens=sum(usage.output_tokens for usage in usages),
        total_tokens=sum(usage.total_tokens for usage in usages),
        latency_p50_ms=_percentile(latencies, 0.5),
        latency_p95_ms=_percentile(latencies, 0.95),
    )


def _has_code(sample: ReviewSampleResult, code: FindingCode) -> bool:
    return bool(
        sample.verifier_report
        and any(finding.code is code for finding in sample.verifier_report.findings)
    )


def _compute_metrics(
    candidates: tuple[ReviewCandidate, ...],
    labels: tuple[ReviewGoldLabel, ...],
    samples: tuple[ReviewSampleResult, ...],
) -> ReviewMetrics:
    candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    label_ids = tuple(label.candidate_id for label in labels)
    sample_ids = tuple(sample.candidate_id for sample in samples)
    if len(label_ids) != len(set(label_ids)):
        raise ValueError("duplicate review label candidate_id in metric inputs")
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("duplicate review sample candidate_id in metric inputs")
    if not (
        len(candidate_ids) == len(label_ids) == len(sample_ids)
        and Counter(candidate_ids) == Counter(label_ids) == Counter(sample_ids)
    ):
        raise ValueError("review metric candidate/label/sample join mismatch")
    labels_by_id = {label.candidate_id: label for label in labels}
    samples_by_id = {sample.candidate_id: sample for sample in samples}
    valid = tuple(
        candidate for candidate in candidates if candidate.mutation_kind is MutationKind.UNMODIFIED
    )
    defects = tuple(
        candidate
        for candidate in candidates
        if candidate.mutation_kind is not MutationKind.UNMODIFIED
    )

    def exact_match(candidate: ReviewCandidate) -> bool:
        label = labels_by_id[candidate.candidate_id]
        verifier_report = samples_by_id[candidate.candidate_id].verifier_report
        return bool(
            verifier_report
            and verifier_report.verdict is label.expected_verdict
            and {finding.code for finding in verifier_report.findings}
            == set(label.expected_finding_codes)
        )

    def valid_pass(candidate: ReviewCandidate) -> bool:
        verifier_report = samples_by_id[candidate.candidate_id].verifier_report
        return bool(
            verifier_report
            and verifier_report.verdict is VerifierVerdict.VERIFIED
            and not verifier_report.findings
            and exact_match(candidate)
        )

    def detection_rate(kind: MutationKind, code: FindingCode) -> float:
        selected = tuple(candidate for candidate in defects if candidate.mutation_kind is kind)
        detected = sum(
            _has_code(samples_by_id[candidate.candidate_id], code) for candidate in selected
        )
        return _ratio(detected, len(selected))

    gap_count = 0
    correct_gaps = 0
    for sample in samples:
        if sample.verifier_report is None:
            continue
        expected = set(labels_by_id[sample.candidate_id].expected_finding_codes)
        for gap in sample.verifier_report.evidence_gaps:
            gap_count += 1
            correct_gaps += gap.finding_code in expected

    valid_passes = sum(valid_pass(candidate) for candidate in valid)
    return ReviewMetrics(
        valid_report_pass_rate=_ratio(valid_passes, len(valid)),
        hard_defect_recall=_ratio(
            sum(exact_match(candidate) for candidate in defects), len(defects)
        ),
        false_block_rate=_ratio(len(valid) - valid_passes, len(valid)),
        unsupported_scope_detection_rate=detection_rate(
            MutationKind.UNSUPPORTED_SCOPE, FindingCode.UNSUPPORTED_SCOPE
        ),
        claim_grounding_detection_rate=detection_rate(
            MutationKind.CLAIM_NOT_GROUNDED, FindingCode.CLAIM_NOT_GROUNDED
        ),
        critical_grounding_detection_rate=detection_rate(
            MutationKind.CRITICAL_SPAN_NOT_GROUNDED,
            FindingCode.CRITICAL_SPAN_NOT_GROUNDED,
        ),
        evidence_gap_precision=_ratio(correct_gaps, gap_count),
        structured_output_success_rate=_ratio(
            sum(sample.verifier_report is not None for sample in samples), len(samples)
        ),
        operational_error_rate=_ratio(
            sum(sample.operational_error is not None for sample in samples), len(samples)
        ),
    )


def _snapshot(trace: TraceIR) -> ReviewInputSnapshot:
    view = DiagnosticTraceView.from_trace(trace)
    return ReviewInputSnapshot(
        trace_id=trace.trace_id,
        run_id=trace.run_id,
        view_json=canonical_json(view),
        input_sha256=canonical_sha256(view),
        catalog_version="evidence-catalog-v1",
        created_at=_SNAPSHOT_TIME,
    )


async def evaluate_review_candidates(
    *,
    candidates: tuple[ReviewCandidate, ...],
    labels: tuple[ReviewGoldLabel, ...],
    traces: tuple[TraceIR, ...],
    verifier: ReviewVerifier,
    policy_version: str,
) -> ReviewEvaluationReport:
    source_run_ids = tuple(trace.run_id for trace in traces)
    if len(source_run_ids) != len(set(source_run_ids)):
        raise ValueError("duplicate Phase 2 source run_id")
    validate_review_cohort(
        candidates, labels, {trace.run_id: trace.trace_id for trace in traces}
    )
    traces_by_run: Mapping[str, TraceIR] = {trace.run_id: trace for trace in traces}
    samples: list[ReviewSampleResult] = []
    for candidate in sorted(
        candidates, key=lambda item: (item.source_run_id, item.mutation_kind.value)
    ):
        input_ = VerificationInput(
            snapshot=_snapshot(traces_by_run[candidate.source_run_id]),
            report=candidate.report,
            report_sha256=canonical_sha256(candidate.report),
        )
        try:
            verifier_report = await verifier.verify(input_)
        except Exception as exc:
            samples.append(
                ReviewSampleResult(
                    candidate_id=candidate.candidate_id,
                    source_run_id=candidate.source_run_id,
                    mutation_kind=candidate.mutation_kind,
                    operational_error=type(exc).__name__,
                )
            )
        else:
            samples.append(
                ReviewSampleResult(
                    candidate_id=candidate.candidate_id,
                    source_run_id=candidate.source_run_id,
                    mutation_kind=candidate.mutation_kind,
                    verifier_report=verifier_report,
                )
            )
    ordered_samples = tuple(samples)
    return ReviewEvaluationReport(
        status=(
            "partial"
            if any(sample.operational_error is not None for sample in ordered_samples)
            else "complete"
        ),
        candidate_count=len(candidates),
        samples=ordered_samples,
        metrics=_compute_metrics(candidates, labels, ordered_samples),
        verifier_version=verifier.version_fingerprint,
        policy_sha256=sha256(policy_version.encode("utf-8")).hexdigest(),
        usage=_usage(ordered_samples),
    )
