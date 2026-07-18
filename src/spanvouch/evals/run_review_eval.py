import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from spanvouch.contracts.trace import TraceIR
from spanvouch.contracts.versioning import canonical_json
from spanvouch.diagnosis.deepseek import DeepSeekConfig, DeepSeekProvider
from spanvouch.diagnosis.errors import ProviderConfigurationError
from spanvouch.evals.generate_review_dataset import DEFAULT_OUTPUT_DATASET, DEFAULT_SOURCE_DATASET
from spanvouch.evals.review_labels import validate_review_dataset
from spanvouch.evals.review_metrics import ReviewEvaluationReport, evaluate_review_candidates
from spanvouch.invariants.supportlab import supportlab_rules
from spanvouch.review.policy import DEFAULT_REVIEW_POLICY_VERSION as DEFAULT_POLICY_VERSION
from spanvouch.review.semantic_verifier import SemanticVerifier
from spanvouch.verification.deterministic import DeterministicVerifier
from spanvouch.verification.invariant_engine import InvariantEngine
from spanvouch.verification.protocols import Verifier


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
    verifier = DeterministicVerifier(
        InvariantEngine(supportlab_rules()), policy_version=policy_version
    )
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
    parser = argparse.ArgumentParser(
        description="Evaluate SpanVouch diagnosis review verification."
    )
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
