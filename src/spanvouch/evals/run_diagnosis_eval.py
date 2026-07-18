import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from pathlib import Path

from spanvouch.contracts.trace import TraceIR
from spanvouch.diagnosis.deepseek import DeepSeekConfig, DeepSeekProvider
from spanvouch.diagnosis.errors import ProviderConfigurationError
from spanvouch.diagnosis.llm_diagnoser import LlmDiagnoser
from spanvouch.diagnosis.models import DiagnoserKind
from spanvouch.diagnosis.rule_diagnoser import RuleDiagnoser
from spanvouch.diagnosis.service import DiagnosisService
from spanvouch.evals.diagnosis_labels import DiagnosisGoldLabel, load_diagnosis_labels
from spanvouch.evals.diagnosis_metrics import DiagnosisEvaluationReport, evaluate_diagnoser
from spanvouch.invariants.engine import InvariantEngine
from spanvouch.invariants.supportlab import supportlab_rules

DEFAULT_DATASET = Path("evals/datasets/supportlab-v1")
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"


def _load_traces(path: Path) -> tuple[TraceIR, ...]:
    return tuple(
        TraceIR.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    )


def _select_run_ids(
    traces: tuple[TraceIR, ...],
    labels: tuple[DiagnosisGoldLabel, ...],
    run_ids: tuple[str, ...],
) -> tuple[tuple[TraceIR, ...], tuple[DiagnosisGoldLabel, ...]]:
    if not run_ids:
        return traces, labels
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("run IDs must be unique")
    traces_by_run = {trace.run_id: trace for trace in traces}
    labels_by_run = {label.run_id: label for label in labels}
    unknown = [
        run_id
        for run_id in run_ids
        if run_id not in traces_by_run or run_id not in labels_by_run
    ]
    if unknown:
        raise ValueError(f"unknown run IDs: {', '.join(unknown)}")
    return (
        tuple(traces_by_run[run_id] for run_id in run_ids),
        tuple(labels_by_run[run_id] for run_id in run_ids),
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


async def _run(
    dataset: Path,
    *,
    kind: DiagnoserKind = DiagnoserKind.RULES,
    run_ids: tuple[str, ...] = (),
    model: str = DEFAULT_DEEPSEEK_MODEL,
) -> DiagnosisEvaluationReport:
    if kind is DiagnoserKind.RULES:
        service = DiagnosisService(
            {DiagnoserKind.RULES: RuleDiagnoser(InvariantEngine(supportlab_rules()))}
        )
    else:
        service = DiagnosisService(
            {
                DiagnoserKind.DEEPSEEK: LlmDiagnoser(
                    DeepSeekProvider(DeepSeekConfig.from_env()),
                    model=model,
                )
            }
        )
    traces, labels = _select_run_ids(
        _load_traces(dataset / "traces.jsonl"),
        load_diagnosis_labels(dataset / "diagnosis-labels-v1.jsonl"),
        run_ids,
    )
    return await evaluate_diagnoser(
        traces=traces,
        labels=labels,
        service=service,
        kind=kind,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate SpanVouch evidence diagnosis.")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--diagnoser",
        choices=tuple(kind.value for kind in DiagnoserKind),
        default=DiagnoserKind.RULES.value,
    )
    parser.add_argument("--run-id", action="append", default=[])
    parser.add_argument(
        "--model",
        default=os.environ.get("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
    )
    parser.add_argument(
        "--allow-live-api",
        action="store_true",
        help="Explicitly permit paid external API calls for the DeepSeek diagnoser.",
    )
    args = parser.parse_args(argv)
    kind = DiagnoserKind(args.diagnoser)
    if kind is DiagnoserKind.DEEPSEEK and not args.allow_live_api:
        parser.error("--diagnoser deepseek requires --allow-live-api")
    try:
        report = asyncio.run(
            _run(
                args.dataset_dir,
                kind=kind,
                run_ids=tuple(args.run_id),
                model=args.model,
            )
        )
    except (ProviderConfigurationError, ValueError) as exc:
        parser.error(str(exc))
    write_report(report, args.output)
    return 0 if report.status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
