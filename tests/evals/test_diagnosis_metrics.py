from pathlib import Path

from spanvouch.contracts.diagnosis import (
    AbstainReason,
    DiagnoserKind,
    DiagnosisDecision,
    DiagnosisExecution,
    DiagnosisProvenance,
    DiagnosisStatus,
    ProviderUsage,
    TaxonomyRef,
)
from spanvouch.contracts.trace import DiagnosticTraceView, TraceIR
from spanvouch.diagnosis.engine import DiagnosisEngine
from spanvouch.diagnosis.rule_diagnoser import RuleDiagnoser
from spanvouch.evals.diagnosis_labels import load_diagnosis_labels
from spanvouch.evals.diagnosis_metrics import evaluate_diagnoser
from spanvouch.invariants.supportlab import supportlab_rules
from spanvouch.trace.evidence_catalog import EvidenceCatalog
from spanvouch.verification.invariant_engine import InvariantEngine

DATASET = Path("evals/datasets/supportlab-v1")


def traces() -> tuple[TraceIR, ...]:
    return tuple(
        TraceIR.model_validate_json(line)
        for line in (DATASET / "traces.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    )


async def test_rule_diagnoser_meets_twenty_trace_hard_gate() -> None:
    service = DiagnosisEngine(
        {
            DiagnoserKind.RULES: RuleDiagnoser(
                InvariantEngine(supportlab_rules())
            )
        }
    )

    report = await evaluate_diagnoser(
        traces=traces(),
        labels=load_diagnosis_labels(DATASET / "diagnosis-labels-v1.jsonl"),
        service=service,
        kind=DiagnoserKind.RULES,
    )

    assert report.status == "complete"
    assert len(report.samples) == 20
    assert report.metrics.supported_accuracy == 1.0
    assert report.metrics.critical_span_top1_accuracy == 1.0
    assert report.metrics.evidence_selector_validity == 1.0
    assert report.metrics.gold_evidence_hit_rate == 1.0
    assert report.metrics.clean_false_positive_rate == 0.0
    assert report.metrics.unsupported_abstain_rate == 1.0
    assert report.metrics.coverage == 0.7
    assert report.metrics.operational_error_rate == 0.0
    assert tuple(item.name for item in report.weak_baselines) == (
        "weak_final_state",
        "weak_rule_only",
    )
    assert all(item.supported_accuracy < 1.0 for item in report.weak_baselines)
    assert report.usage.provider_sample_count == 0
    assert report.usage.total_tokens == 0
    assert report.usage.latency_p50_ms is None
    assert report.usage.latency_p95_ms is None


class _UsageDiagnoser:
    version_fingerprint = "usage-test-v1"

    def __init__(self) -> None:
        self.calls = 0

    async def diagnose(
        self, view: DiagnosticTraceView, evidence: EvidenceCatalog
    ) -> DiagnosisExecution:
        self.calls += 1
        latency = 10.0 if self.calls == 1 else 30.0
        return DiagnosisExecution(
            decision=DiagnosisDecision(
                status=DiagnosisStatus.NO_FAILURE,
                failure_type="no_failure",
                confidence=1.0,
            ),
            provenance=DiagnosisProvenance(
                taxonomy=TaxonomyRef(taxonomy_id="supportlab", taxonomy_version="1.0"),
                diagnoser_version="usage-test-v1",
                model="test-model",
                provider="test-provider",
            ),
            usage=ProviderUsage(
                input_tokens=10 * self.calls,
                output_tokens=2 * self.calls,
                total_tokens=12 * self.calls,
                latency_ms=latency,
                request_id=f"request-{self.calls}",
            ),
        )


async def test_evaluation_aggregates_provider_usage_and_latency_percentiles() -> None:
    all_traces = traces()
    all_labels = load_diagnosis_labels(DATASET / "diagnosis-labels-v1.jsonl")
    run_ids = ("clean-01", "clean-02")
    selected_traces = tuple(trace for trace in all_traces if trace.run_id in run_ids)
    selected_labels = tuple(label for label in all_labels if label.run_id in run_ids)

    report = await evaluate_diagnoser(
        traces=selected_traces,
        labels=selected_labels,
        service=DiagnosisEngine({DiagnoserKind.DEEPSEEK: _UsageDiagnoser()}),
        kind=DiagnoserKind.DEEPSEEK,
    )

    assert report.usage.provider_sample_count == 2
    assert report.usage.input_tokens == 30
    assert report.usage.output_tokens == 6
    assert report.usage.total_tokens == 36
    assert report.usage.latency_p50_ms == 20.0
    assert report.usage.latency_p95_ms == 29.0
    assert report.usage.estimated_cost_usd is None


class _InvalidOutputDiagnoser:
    version_fingerprint = "invalid-output-v1"

    async def diagnose(
        self, view: DiagnosticTraceView, evidence: EvidenceCatalog
    ) -> DiagnosisExecution:
        return DiagnosisExecution(
            decision=DiagnosisDecision(
                status=DiagnosisStatus.ABSTAINED,
                confidence=0.0,
                abstain_reason=AbstainReason.INVALID_MODEL_OUTPUT,
            ),
            provenance=DiagnosisProvenance(
                taxonomy=TaxonomyRef(taxonomy_id="supportlab", taxonomy_version="1.0"),
                diagnoser_version="invalid-output-v1",
                model="test-model",
                provider="test-provider",
            ),
        )


async def test_invalid_model_output_is_not_counted_as_structured_success() -> None:
    selected_trace = tuple(trace for trace in traces() if trace.run_id == "clean-01")
    selected_label = tuple(
        label
        for label in load_diagnosis_labels(DATASET / "diagnosis-labels-v1.jsonl")
        if label.run_id == "clean-01"
    )

    report = await evaluate_diagnoser(
        traces=selected_trace,
        labels=selected_label,
        service=DiagnosisEngine(
            {DiagnoserKind.DEEPSEEK: _InvalidOutputDiagnoser()}
        ),
        kind=DiagnoserKind.DEEPSEEK,
    )

    assert report.metrics.structured_output_success_rate == 0.0
    assert report.metrics.semantic_abstain_rate == 1.0
