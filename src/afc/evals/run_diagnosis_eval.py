import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from afc.diagnosis.models import DiagnoserKind
from afc.diagnosis.rule_diagnoser import RuleDiagnoser
from afc.diagnosis.service import DiagnosisService
from afc.evals.diagnosis_labels import load_diagnosis_labels
from afc.evals.diagnosis_metrics import DiagnosisEvaluationReport, evaluate_diagnoser
from afc.invariants.engine import InvariantEngine
from afc.invariants.supportlab import supportlab_rules
from afc.trace_ir.models import TraceIR

DEFAULT_DATASET = Path("evals/datasets/supportlab-v1")


def _load_traces(path: Path) -> tuple[TraceIR, ...]:
    return tuple(
        TraceIR.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    )


def write_report(report: DiagnosisEvaluationReport, path: Path) -> None:
    content = json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{content}\n", encoding="utf-8", newline="\n")


async def _run(dataset: Path) -> DiagnosisEvaluationReport:
    service = DiagnosisService(
        {
            DiagnoserKind.RULES: RuleDiagnoser(
                InvariantEngine(supportlab_rules())
            )
        }
    )
    return await evaluate_diagnoser(
        traces=_load_traces(dataset / "traces.jsonl"),
        labels=load_diagnosis_labels(dataset / "diagnosis-labels-v1.jsonl"),
        service=service,
        kind=DiagnoserKind.RULES,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate AFC evidence diagnosis.")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = asyncio.run(_run(args.dataset_dir))
    write_report(report, args.output)
    return 0 if report.status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
