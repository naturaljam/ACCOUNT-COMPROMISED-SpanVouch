import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from afc.diagnosis.deepseek import DeepSeekConfig, DeepSeekProvider
from afc.diagnosis.errors import ProviderConfigurationError
from afc.evals.generate_review_dataset import DEFAULT_OUTPUT_DATASET, DEFAULT_SOURCE_DATASET
from afc.evals.review_labels import validate_review_dataset
from afc.evals.review_metrics import ReviewEvaluationReport, evaluate_review_candidates
from afc.invariants.engine import InvariantEngine
from afc.invariants.supportlab import supportlab_rules
from afc.review.evidence_verifier import EvidenceVerifier
from afc.review.models import canonical_json
from afc.review.policy import DEFAULT_REVIEW_POLICY_VERSION as DEFAULT_POLICY_VERSION
from afc.review.protocols import Verifier
from afc.review.semantic_verifier import SemanticVerifier
from afc.trace_ir.models import TraceIR


def _load_traces(path: Path) -> tuple[TraceIR, ...]:
    return tuple(
        TraceIR.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    )


def write_report(report: ReviewEvaluationReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(report) + "\n", encoding="utf-8", newline="\n")


async def _run(
    dataset: Path,
    source_dataset: Path,
    *,
    policy_version: str = DEFAULT_POLICY_VERSION,
    semantic_verifier: Verifier | None = None,
    semantic_candidate_ids: tuple[str, ...] = (),
) -> ReviewEvaluationReport:
    candidates, labels, _ = validate_review_dataset(dataset, source_dataset)
    verifier = EvidenceVerifier(InvariantEngine(supportlab_rules()), policy_version=policy_version)
    return await evaluate_review_candidates(
        candidates=candidates,
        labels=labels,
        traces=_load_traces(source_dataset / "traces.jsonl"),
        verifier=verifier,
        policy_version=policy_version,
        semantic_verifier=semantic_verifier,
        semantic_candidate_ids=semantic_candidate_ids,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate AFC diagnosis review verification.")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_OUTPUT_DATASET)
    parser.add_argument("--source-dataset-dir", type=Path, default=DEFAULT_SOURCE_DATASET)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verifier", choices=("deterministic", "hybrid"), default="deterministic")
    parser.add_argument("--candidate-id", action="append", default=[])
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--allow-live-api", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.verifier == "hybrid" and not arguments.allow_live_api:
        parser.error("hybrid verifier requires --allow-live-api")
    semantic_verifier: Verifier | None = None
    if arguments.verifier == "hybrid":
        try:
            config = DeepSeekConfig.from_env()
        except ProviderConfigurationError as exc:
            parser.error(str(exc))
        semantic_verifier = SemanticVerifier(DeepSeekProvider(config), model=arguments.model)
    try:
        report = asyncio.run(
            _run(
                arguments.dataset_dir,
                arguments.source_dataset_dir,
                semantic_verifier=semantic_verifier,
                semantic_candidate_ids=tuple(arguments.candidate_id),
            )
        )
    except ValueError as exc:
        parser.error(str(exc))
    write_report(report, arguments.output)
    return 0 if report.status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
