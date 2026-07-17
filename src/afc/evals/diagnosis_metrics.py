from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from afc.diagnosis.errors import DiagnosisError
from afc.diagnosis.evidence import EvidenceCatalog
from afc.diagnosis.models import DiagnoserKind, DiagnosisReport, DiagnosisStatus
from afc.diagnosis.service import DiagnosisService
from afc.diagnosis.trace_view import DiagnosticTraceView
from afc.evals.baselines import final_state_baseline, rule_only_baseline
from afc.evals.diagnosis_labels import DiagnosisGoldLabel, validate_dataset_join
from afc.failure_types import FailureType
from afc.trace_ir.models import TraceIR


class DiagnosisSampleResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    gold_failure_type: FailureType
    gold_status: DiagnosisStatus
    report: DiagnosisReport | None = None
    operational_error: str | None = None


class DiagnosisMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    supported_accuracy: float | None
    critical_span_top1_accuracy: float | None
    evidence_selector_validity: float | None
    gold_evidence_hit_rate: float | None
    evidence_precision: float | None
    clean_false_positive_rate: float | None
    unsupported_abstain_rate: float | None
    coverage: float
    structured_output_success_rate: float
    semantic_abstain_rate: float
    operational_error_rate: float


class WeakBaselineSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal["weak_final_state", "weak_rule_only"]
    supported_accuracy: float
    coverage: float


class DiagnosisEvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["complete", "partial"]
    diagnoser: DiagnoserKind
    trace_count: int = Field(ge=1)
    samples: tuple[DiagnosisSampleResult, ...]
    metrics: DiagnosisMetrics
    weak_baselines: tuple[WeakBaselineSummary, ...]


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


async def evaluate_diagnoser(
    *,
    traces: tuple[TraceIR, ...],
    labels: tuple[DiagnosisGoldLabel, ...],
    service: DiagnosisService,
    kind: DiagnoserKind,
) -> DiagnosisEvaluationReport:
    validate_dataset_join(traces, labels)
    traces_by_run = {trace.run_id: trace for trace in traces}
    samples: list[DiagnosisSampleResult] = []
    for label in labels:
        try:
            report = await service.diagnose(traces_by_run[label.run_id], kind)
        except DiagnosisError as exc:
            samples.append(
                DiagnosisSampleResult(
                    run_id=label.run_id,
                    gold_failure_type=label.failure_type,
                    gold_status=label.expected_status,
                    operational_error=type(exc).__name__,
                )
            )
        else:
            samples.append(
                DiagnosisSampleResult(
                    run_id=label.run_id,
                    gold_failure_type=label.failure_type,
                    gold_status=label.expected_status,
                    report=report,
                )
            )
    metrics = _compute_metrics(traces_by_run, labels, tuple(samples))
    return DiagnosisEvaluationReport(
        status="partial" if any(sample.operational_error for sample in samples) else "complete",
        diagnoser=kind,
        trace_count=len(traces),
        samples=tuple(samples),
        metrics=metrics,
        weak_baselines=_evaluate_weak_baselines(traces_by_run, labels),
    )


def _evaluate_weak_baselines(
    traces: dict[str, TraceIR], labels: tuple[DiagnosisGoldLabel, ...]
) -> tuple[WeakBaselineSummary, ...]:
    supported = tuple(
        label for label in labels if label.expected_status is not DiagnosisStatus.ABSTAINED
    )
    final_correct = 0
    rule_correct = 0
    for label in supported:
        trace = traces[label.run_id]
        root = next(span for span in trace.spans if span.parent_span_id is None)
        final_prediction = final_state_baseline(
            outcome=str(root.attributes.get("run.outcome", "unknown")),
            final_message=(
                str(root.attributes["run.final_message"])
                if "run.final_message" in root.attributes
                else None
            ),
        )
        observations = tuple(
            f"{span.attributes.get('tool.error.type', '')}:"
            f"{span.attributes.get('tool.error.message', '')}"
            for span in trace.spans
            if "tool.error.type" in span.attributes
        )
        tool_steps = sum(span.kind.value == "tool" for span in trace.spans)
        rule_prediction = rule_only_baseline(
            observations=observations,
            steps=tool_steps,
            max_steps=8,
        )
        final_correct += final_prediction.failure_type is label.failure_type
        rule_correct += rule_prediction.failure_type is label.failure_type
    denominator = len(supported)
    return (
        WeakBaselineSummary(
            name="weak_final_state",
            supported_accuracy=final_correct / denominator,
            coverage=1.0,
        ),
        WeakBaselineSummary(
            name="weak_rule_only",
            supported_accuracy=rule_correct / denominator,
            coverage=1.0,
        ),
    )


def _compute_metrics(
    traces: dict[str, TraceIR],
    labels: tuple[DiagnosisGoldLabel, ...],
    samples: tuple[DiagnosisSampleResult, ...],
) -> DiagnosisMetrics:
    samples_by_run = {sample.run_id: sample for sample in samples}
    supported = tuple(
        label for label in labels if label.expected_status is not DiagnosisStatus.ABSTAINED
    )
    diagnosed = tuple(
        label for label in labels if label.expected_status is DiagnosisStatus.DIAGNOSED
    )
    clean = tuple(
        label for label in labels if label.expected_status is DiagnosisStatus.NO_FAILURE
    )
    unsupported = tuple(
        label for label in labels if label.expected_status is DiagnosisStatus.ABSTAINED
    )

    def correct(label: DiagnosisGoldLabel) -> bool:
        report = samples_by_run[label.run_id].report
        return bool(
            report
            and report.status is label.expected_status
            and report.failure_type is label.failure_type
        )

    def has_status(label: DiagnosisGoldLabel, status: DiagnosisStatus) -> bool:
        report = samples_by_run[label.run_id].report
        return report is not None and report.status is status

    critical_correct = 0
    evidence_hits = 0
    predicted_evidence = 0
    relevant_evidence = 0
    valid_evidence = 0
    total_evidence = 0
    for label in diagnosed:
        sample = samples_by_run[label.run_id]
        if sample.report is None:
            continue
        if (
            sample.report.critical_span_ids
            and sample.report.critical_span_ids[0]
            in label.acceptable_critical_span_ids
        ):
            critical_correct += 1
        predicted = {item.canonical for item in sample.report.evidence}
        gold = {item.canonical for item in label.acceptable_evidence}
        if predicted & gold:
            evidence_hits += 1
        predicted_evidence += len(predicted)
        relevant_evidence += len(predicted & gold)

    for sample in samples:
        if sample.report is None:
            continue
        catalog = EvidenceCatalog.from_view(
            DiagnosticTraceView.from_trace(traces[sample.run_id])
        )
        selectors = set(catalog.selectors)
        for item in sample.report.evidence:
            total_evidence += 1
            if item.canonical in selectors:
                valid_evidence += 1

    reports = tuple(sample.report for sample in samples if sample.report is not None)
    return DiagnosisMetrics(
        supported_accuracy=_ratio(sum(correct(label) for label in supported), len(supported)),
        critical_span_top1_accuracy=_ratio(critical_correct, len(diagnosed)),
        evidence_selector_validity=_ratio(valid_evidence, total_evidence),
        gold_evidence_hit_rate=_ratio(evidence_hits, len(diagnosed)),
        evidence_precision=_ratio(relevant_evidence, predicted_evidence),
        clean_false_positive_rate=_ratio(
            sum(has_status(label, DiagnosisStatus.DIAGNOSED) for label in clean),
            len(clean),
        ),
        unsupported_abstain_rate=_ratio(
            sum(has_status(label, DiagnosisStatus.ABSTAINED) for label in unsupported),
            len(unsupported),
        ),
        coverage=sum(report.status is not DiagnosisStatus.ABSTAINED for report in reports)
        / len(labels),
        structured_output_success_rate=len(reports) / len(labels),
        semantic_abstain_rate=sum(
            report.status is DiagnosisStatus.ABSTAINED for report in reports
        )
        / len(labels),
        operational_error_rate=sum(sample.operational_error is not None for sample in samples)
        / len(labels),
    )
