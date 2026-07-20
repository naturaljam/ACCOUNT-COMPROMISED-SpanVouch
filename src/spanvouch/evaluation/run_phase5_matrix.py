"""Provider-phase CLI boundary for the Phase 5 verification matrix."""

from __future__ import annotations

import argparse
import asyncio
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from spanvouch.contracts.versioning import SHA256_PATTERN, canonical_sha256
from spanvouch.evaluation.artifacts import read_verified_directory_tree
from spanvouch.evaluation.corpus import CorpusEntry
from spanvouch.evaluation.corpus.repository import TraceReplayRepository
from spanvouch.evaluation.experiments.config import (
    ConditionId,
    load_experiment_config,
)
from spanvouch.evaluation.experiments.diagnosis import (
    DiagnosisCandidateRepository,
    FrozenDiagnosisCandidate,
)
from spanvouch.evaluation.experiments.models import (
    ConditionPlan,
    ConditionResult,
    ConditionStatus,
    ExperimentFailureCategory,
    ExperimentMatrixManifest,
    SelectiveAction,
)
from spanvouch.evaluation.experiments.planner import VerificationMatrixPlanner
from spanvouch.evaluation.experiments.provider import (
    PaidRunAuthorization,
    ProviderConfigurationError,
)
from spanvouch.evaluation.experiments.runner import (
    ExperimentRunner,
    ProviderPhaseRepository,
    RunnerExecutionError,
)


@dataclass(frozen=True)
class ProviderRunRequest:
    config: Path
    corpus_dir: Path
    candidate_dir: Path
    output_dir: Path
    allow_live_provider: bool
    formal_run: bool
    approved_manifest_sha256: str | None


ProviderRunCommand = Callable[[ProviderRunRequest], None]


def _require_approved_manifest(
    request: ProviderRunRequest,
    *,
    matrix_manifest_sha256: str,
) -> str:
    """Bind paid execution to the exact matrix identity approved beforehand."""
    approved = request.approved_manifest_sha256
    if request.formal_run and approved is None:
        raise ProviderConfigurationError(
            "formal live run requires --approved-manifest-sha256"
        )
    if approved is not None and re.fullmatch(SHA256_PATTERN, approved) is None:
        raise ProviderConfigurationError("approved manifest hash must be SHA-256")
    if approved is not None and approved != matrix_manifest_sha256:
        raise ProviderConfigurationError("approved manifest hash does not match matrix")
    return matrix_manifest_sha256


class _OfflineExecutor:
    """Run free controls and fail closed on provider cache misses."""

    async def execute(self, plan: ConditionPlan) -> ConditionResult:
        if plan.condition_id not in {ConditionId.B0, ConditionId.B1}:
            raise RunnerExecutionError(
                ExperimentFailureCategory.PROVIDER, "cache-miss-live-disabled"
            )
        now = datetime.now(UTC)
        return ConditionResult(
            plan_id=plan.plan_id,
            cell=plan.cell,
            record_sha256=plan.record_sha256,
            trace_sha256=plan.trace_sha256,
            diagnosis_sha256=plan.diagnosis_sha256,
            condition_id=plan.condition_id,
            status=ConditionStatus.COMPLETED,
            selective_action=(
                SelectiveAction.ACCEPT
                if plan.condition_id is ConditionId.B0
                else SelectiveAction.ABSTAIN
            ),
            verifier_report_sha256s=(),
            request_audit_sha256s=(),
            cache_status="not_required",
            started_at_utc=now,
            completed_at_utc=now,
        )


def _load_candidates(
    root: Path,
    entries: tuple[CorpusEntry, ...],
    corpus_manifest_sha256: str,
) -> tuple[FrozenDiagnosisCandidate, ...]:
    snapshot = read_verified_directory_tree(root)
    expected_files: set[str] = set()
    repository = DiagnosisCandidateRepository(root)
    candidates: list[FrozenDiagnosisCandidate] = []
    for entry in entries:
        identity = canonical_sha256(entry.cell)[:16]
        prefix = f"cells/{identity}/"
        matches = [path for path in snapshot.files if path.startswith(prefix)]
        if len(matches) != 1:
            raise ValueError("candidate repository cell has unexpected layout")
        digest = Path(matches[0]).stem
        expected_files.add(matches[0])
        candidates.append(
            repository.load(
                entry.cell,
                expected_candidate_sha256=digest,
                expected_corpus_manifest_sha256=corpus_manifest_sha256,
            )
        )
    if set(snapshot.files) != expected_files:
        raise ValueError("candidate repository contains cells outside the corpus")
    return tuple(candidates)


def _default_command(request: ProviderRunRequest) -> None:
    config = load_experiment_config(request.config)
    corpus = TraceReplayRepository(request.corpus_dir)
    corpus_manifest = corpus.verify()
    for entry in corpus_manifest.entries:
        if CorpusEntry.from_record(corpus.load(entry.cell)) != entry:
            raise ValueError("corpus entry failed reconstructive verification")
    candidates = _load_candidates(
        request.candidate_dir, corpus_manifest.entries, corpus.manifest_sha256
    )
    expected_cells = tuple(entry.cell for entry in corpus_manifest.entries)
    planner = VerificationMatrixPlanner()
    plans = planner.plan(candidates, config, expected_cells=expected_cells)
    candidate_manifest_sha256 = canonical_sha256(
        cast(JsonValue, [item.model_dump(mode="json") for item in candidates])
    )
    matrix = ExperimentMatrixManifest.from_plans(
        plans=plans,
        candidates=candidates,
        config=config,
        candidate_manifest_sha256=candidate_manifest_sha256,
        ineligible=(),
        expected_cells=expected_cells,
    )
    if request.allow_live_provider or request.formal_run:
        matrix_manifest_sha256 = _require_approved_manifest(
            request,
            matrix_manifest_sha256=canonical_sha256(matrix),
        )
        PaidRunAuthorization(
            experiment_id=config.experiment_id,
            allow_live_provider=request.allow_live_provider,
            formal_run=request.formal_run,
            frozen_manifest_sha256=matrix_manifest_sha256,
        ).require(config.mode)
        raise RuntimeError("live provider adapter is not configured for this CLI")
    asyncio.run(
        ExperimentRunner(executor=_OfflineExecutor()).run_provider_phase(
            plans=plans,
            matrix=matrix,
            repository=ProviderPhaseRepository(request.output_dir),
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spanvouch experiments run")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--corpus-dir", required=True, type=Path)
    parser.add_argument("--candidate-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--allow-live-provider", action="store_true")
    parser.add_argument("--formal-run", action="store_true")
    parser.add_argument("--approved-manifest-sha256")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    command: ProviderRunCommand | None = None,
) -> int:
    arguments = build_parser().parse_args(argv)
    request = ProviderRunRequest(
        config=arguments.config,
        corpus_dir=arguments.corpus_dir,
        candidate_dir=arguments.candidate_dir,
        output_dir=arguments.output_dir,
        allow_live_provider=arguments.allow_live_provider,
        formal_run=arguments.formal_run,
        approved_manifest_sha256=arguments.approved_manifest_sha256,
    )
    (command or _default_command)(request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
