import argparse
import asyncio
import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from spanvouch.contracts.trace import TraceIR, TraceSpan
from spanvouch.observability.tracing import build_test_tracer
from spanvouch.supportlab.decision import ScriptedDecisionModel
from spanvouch.supportlab.graph import run_support_scenario
from spanvouch.supportlab.repository import build_seed_repository
from spanvouch.supportlab.scenarios import build_scenarios
from spanvouch.supportlab.tools import SupportTools
from spanvouch.trace.mapper import map_spans


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


def _order_span_tree(trace: TraceIR) -> tuple[list[TraceSpan], dict[str, list[TraceSpan]]]:
    spans_by_id = {span.span_id: span for span in trace.spans}
    children_by_parent: dict[str, list[TraceSpan]] = {}
    roots: list[TraceSpan] = []

    for span in trace.spans:
        if span.parent_span_id is None:
            roots.append(span)
            continue
        if span.parent_span_id not in spans_by_id:
            raise ValueError(f"missing parent span: {span.parent_span_id}")
        children_by_parent.setdefault(span.parent_span_id, []).append(span)

    visit_state: dict[str, int] = {}

    def check_acyclic(span: TraceSpan) -> None:
        state = visit_state.get(span.span_id, 0)
        if state == 1:
            raise ValueError(f"parent cycle detected at span: {span.span_id}")
        if state == 2:
            return
        visit_state[span.span_id] = 1
        if span.parent_span_id is not None:
            check_acyclic(spans_by_id[span.parent_span_id])
        visit_state[span.span_id] = 2

    for span in trace.spans:
        check_acyclic(span)

    if len(roots) != 1:
        raise ValueError(f"trace must contain exactly one root span; found {len(roots)}")

    ordered: list[TraceSpan] = []

    def append_subtree(span: TraceSpan) -> None:
        ordered.append(span)
        for child in children_by_parent.get(span.span_id, []):
            append_subtree(child)

    append_subtree(roots[0])
    return ordered, children_by_parent


def _normalize_trace(trace: TraceIR, sequence: int) -> TraceIR:
    # Children retain their source-list order, which is the exporter's stable finish order.
    ordered, children_by_parent = _order_span_tree(trace)
    id_map = {span.span_id: f"span-{index:03d}" for index, span in enumerate(ordered)}
    trace_id = f"supportlab-trace-{sequence:03d}"
    base_time = datetime(2026, 7, 15, tzinfo=UTC) + timedelta(seconds=sequence)
    start_times = {
        span.span_id: base_time + timedelta(milliseconds=index * 10)
        for index, span in enumerate(ordered)
    }
    end_times: dict[str, datetime] = {}
    for span in reversed(ordered):
        own_end = start_times[span.span_id] + timedelta(milliseconds=5)
        child_ends = [
            end_times[child.span_id] for child in children_by_parent.get(span.span_id, [])
        ]
        end_times[span.span_id] = max([own_end, *child_ends])

    normalized = []
    for span in ordered:
        normalized.append(
            span.model_copy(
                update={
                    "trace_id": trace_id,
                    "span_id": id_map[span.span_id],
                    "parent_span_id": (
                        id_map[span.parent_span_id] if span.parent_span_id is not None else None
                    ),
                    "started_at": start_times[span.span_id],
                    "ended_at": end_times[span.span_id],
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
        trace_rows.append(trace.model_dump(mode="json", exclude={"schema_name"}))
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("evals/datasets/supportlab-v1"))
    parser.add_argument("--seed", type=int, default=20260715)
    arguments = parser.parse_args(argv)
    asyncio.run(generate_dataset(arguments.output, arguments.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
