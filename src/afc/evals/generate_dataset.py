import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from afc.observability.tracing import build_test_tracer
from afc.supportlab.decision import ScriptedDecisionModel
from afc.supportlab.graph import run_support_scenario
from afc.supportlab.repository import build_seed_repository
from afc.supportlab.scenarios import build_scenarios
from afc.supportlab.tools import SupportTools
from afc.trace_ir.mapper import map_spans
from afc.trace_ir.models import TraceIR


class DatasetManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = "supportlab-v1"
    schema_version: str = "1.0"
    seed: int
    trace_count: int
    traces_sha256: str
    labels_sha256: str


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> str:
    content = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    path.write_text(content, encoding="utf-8", newline="\n")
    return hashlib.sha256(content.encode()).hexdigest()


def _normalize_trace(trace: TraceIR, sequence: int) -> TraceIR:
    ordered = sorted(trace.spans, key=lambda span: span.parent_span_id is not None)
    id_map = {span.span_id: f"span-{index:03d}" for index, span in enumerate(ordered)}
    trace_id = f"supportlab-trace-{sequence:03d}"
    base_time = datetime(2026, 7, 15, tzinfo=UTC) + timedelta(seconds=sequence)
    normalized = []
    for index, span in enumerate(ordered):
        started_at = base_time + timedelta(milliseconds=index * 10)
        normalized.append(
            span.model_copy(
                update={
                    "trace_id": trace_id,
                    "span_id": id_map[span.span_id],
                    "parent_span_id": (
                        id_map[span.parent_span_id] if span.parent_span_id is not None else None
                    ),
                    "started_at": started_at,
                    "ended_at": started_at + timedelta(milliseconds=5),
                }
            )
        )
    return TraceIR(
        trace_id=trace_id,
        run_id=trace.run_id,
        spans=normalized,
    )


async def generate_dataset(output_dir: Path, seed: int = 20260715) -> DatasetManifest:
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_rows: list[dict[str, object]] = []
    label_rows: list[dict[str, object]] = []
    for sequence, scenario in enumerate(build_scenarios(seed), start=1):
        tracer, exporter = build_test_tracer()
        result = await run_support_scenario(
            scenario=scenario,
            tools=SupportTools(build_seed_repository()),
            decision_model=ScriptedDecisionModel(scenario),
            tracer=tracer,
        )
        trace = _normalize_trace(
            map_spans(scenario.scenario_id, exporter.get_finished_spans()),
            sequence,
        )
        trace_rows.append(trace.model_dump(mode="json"))
        label_rows.append(
            {
                "run_id": scenario.scenario_id,
                "failure_type": scenario.expected_failure.value,
                "critical_operation": scenario.expected_critical_operation,
                "observed_outcome": result.outcome.value,
            }
        )
    traces_hash = _write_jsonl(output_dir / "traces.jsonl", trace_rows)
    labels_hash = _write_jsonl(output_dir / "labels.jsonl", label_rows)
    manifest = DatasetManifest(
        seed=seed,
        trace_count=len(trace_rows),
        traces_sha256=traces_hash,
        labels_sha256=labels_hash,
    )
    (output_dir / "manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("evals/datasets/supportlab-v1"))
    parser.add_argument("--seed", type=int, default=20260715)
    arguments = parser.parse_args()
    asyncio.run(generate_dataset(arguments.output, arguments.seed))


if __name__ == "__main__":
    main()
