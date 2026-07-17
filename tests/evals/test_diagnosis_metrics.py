from pathlib import Path

from afc.diagnosis.models import DiagnoserKind
from afc.diagnosis.rule_diagnoser import RuleDiagnoser
from afc.diagnosis.service import DiagnosisService
from afc.evals.diagnosis_labels import load_diagnosis_labels
from afc.evals.diagnosis_metrics import evaluate_diagnoser
from afc.invariants.engine import InvariantEngine
from afc.invariants.supportlab import supportlab_rules
from afc.trace_ir.models import TraceIR

DATASET = Path("evals/datasets/supportlab-v1")


def traces() -> tuple[TraceIR, ...]:
    return tuple(
        TraceIR.model_validate_json(line)
        for line in (DATASET / "traces.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    )


async def test_rule_diagnoser_meets_twenty_trace_hard_gate() -> None:
    service = DiagnosisService(
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
