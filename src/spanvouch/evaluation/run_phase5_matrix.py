"""Provider-phase CLI boundary for the Phase 5 verification matrix."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
from pydantic import JsonValue, SecretStr

from spanvouch.adapters.models.deepseek import DeepSeekConfig, DeepSeekProvider
from spanvouch.adapters.models.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from spanvouch.contracts.verification import ReviewInputSnapshot, VerificationInput
from spanvouch.contracts.versioning import (
    SHA256_PATTERN,
    canonical_json,
    canonical_sha256,
)
from spanvouch.diagnosis.protocols import ChatMessage, ModelProvider
from spanvouch.evaluation.artifacts import read_verified_directory_tree
from spanvouch.evaluation.corpus import CorpusEntry
from spanvouch.evaluation.corpus.repository import TraceReplayRepository
from spanvouch.evaluation.experiments.budget import BudgetLedger, Pricing
from spanvouch.evaluation.experiments.conditions import (
    ConditionExecutionContext,
    ConditionExecutor,
)
from spanvouch.evaluation.experiments.config import (
    ConditionId,
    Phase5ExperimentConfig,
    load_experiment_config,
)
from spanvouch.evaluation.experiments.diagnosis import (
    DiagnosisCandidateRepository,
    FrozenDiagnosisCandidate,
    reconstruct_shared_verifier_messages,
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
    GuardedProvider,
    PaidRunAuthorization,
    ProviderConfigurationError,
    ProviderResultCache,
    RequestIdentity,
)
from spanvouch.evaluation.experiments.runner import (
    ExperimentRunner,
    ProviderPhaseRepository,
    RunnerExecutionError,
)
from spanvouch.labs.opslab.invariants import opslab_rules
from spanvouch.labs.supportlab.invariants import supportlab_rules
from spanvouch.verification.deterministic import DeterministicVerifier
from spanvouch.verification.invariant_engine import InvariantEngine

_VERIFIER_INSTRUCTION = "Critique evidence sufficiency only."
_POLICY_VERSION = "phase5-deterministic-v1"


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


def _required_environment(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ProviderConfigurationError(f"{name} is not configured")
    return value


def _load_pricing(path: str, provider: str, model: str) -> Pricing:
    try:
        pricing = Pricing.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProviderConfigurationError("provider pricing is missing or invalid") from error
    pricing.require_endpoint(provider, model)
    return pricing


@dataclass(frozen=True)
class _LiveProviderComposition:
    deepseek: ModelProvider
    qwen: ModelProvider
    pricing: Mapping[str, Pricing]


def _compose_live_providers(
    config: Phase5ExperimentConfig,
    *,
    environ: Mapping[str, str],
    deepseek_client: httpx.AsyncClient | None = None,
    qwen_client: httpx.AsyncClient | None = None,
) -> _LiveProviderComposition:
    deepseek_endpoint = config.shared_verifier
    qwen_endpoint = config.cross_model_verifier
    if (
        deepseek_endpoint.provider != "deepseek"
        or config.isolated_verifier.provider != "deepseek"
        or qwen_endpoint.provider != "qwen"
    ):
        raise ProviderConfigurationError("experiment provider endpoints are unsupported")
    deepseek_key = _required_environment(environ, "DEEPSEEK_API_KEY")
    qwen_key = _required_environment(environ, "SPANVOUCH_VLLM_API_KEY")
    qwen_config = OpenAICompatibleConfig(
        api_key=SecretStr(qwen_key),
        base_url=_required_environment(environ, "SPANVOUCH_VLLM_BASE_URL"),
        expected_model=qwen_endpoint.model,
        endpoint_class=qwen_endpoint.endpoint_class,
        smoke_only=False,
        container_repo_digest=_required_environment(
            environ, "SPANVOUCH_VLLM_CONTAINER_REPO_DIGEST"
        ),
        hf_revision=_required_environment(environ, "SPANVOUCH_VLLM_HF_REVISION"),
    ).validate_for_experiment(config.mode.value)
    deepseek_pricing = _load_pricing(
        _required_environment(environ, "SPANVOUCH_PHASE5_DEEPSEEK_PRICING_PATH"),
        deepseek_endpoint.provider,
        deepseek_endpoint.model,
    )
    qwen_pricing = _load_pricing(
        _required_environment(environ, "SPANVOUCH_PHASE5_QWEN_PRICING_PATH"),
        qwen_endpoint.provider,
        qwen_endpoint.model,
    )
    return _LiveProviderComposition(
        deepseek=DeepSeekProvider(
            DeepSeekConfig(api_key=SecretStr(deepseek_key)), client=deepseek_client
        ),
        qwen=OpenAICompatibleProvider(qwen_config, client=qwen_client),
        pricing={"deepseek": deepseek_pricing, "qwen": qwen_pricing},
    )


class _LiveConditionExecutor:
    def __init__(
        self,
        *,
        candidates: tuple[FrozenDiagnosisCandidate, ...],
        config: Phase5ExperimentConfig,
        providers: _LiveProviderComposition,
        authorization: PaidRunAuthorization,
        database_path: Path,
        condition_executor: ConditionExecutor | None = None,
    ) -> None:
        self._candidates = {candidate.cell: candidate for candidate in candidates}
        self._config = config
        self._providers = providers
        self._authorization = authorization
        self._cache = ProviderResultCache(database_path)
        self._ledger = BudgetLedger(database_path, config.budget)
        self._conditions = condition_executor or ConditionExecutor()

    @staticmethod
    def _verification_input(candidate: FrozenDiagnosisCandidate) -> VerificationInput:
        view = candidate.diagnostic_context.view
        created_at = min(span.started_at for span in view.spans)
        return VerificationInput(
            snapshot=ReviewInputSnapshot(
                trace_id=candidate.diagnostic_context.trace_id,
                run_id=candidate.diagnostic_context.run_id,
                view_json=canonical_json(view),
                input_sha256=canonical_sha256(view),
                catalog_version="evidence-catalog-v1",
                created_at=created_at,
            ),
            report=candidate.report,
            report_sha256=candidate.report_sha256,
        )

    @staticmethod
    def _deterministic(candidate: FrozenDiagnosisCandidate) -> DeterministicVerifier:
        if candidate.cell.domain == "supportlab":
            rules = supportlab_rules()
        elif candidate.cell.domain == "opslab":
            rules = opslab_rules()
        else:
            raise ProviderConfigurationError("unsupported experiment domain")
        return DeterministicVerifier(
            InvariantEngine(rules), policy_version=_POLICY_VERSION
        )

    def _guarded_provider(
        self,
        plan: ConditionPlan,
        verification_input: VerificationInput,
        diagnosis_messages: tuple[ChatMessage, ...],
    ) -> GuardedProvider:
        if plan.provider is None or plan.model is None:
            raise ProviderConfigurationError("paid condition has no provider endpoint")
        prepared = ConditionExecutor._prepare(
            plan,
            verification_input,
            diagnosis_messages if plan.condition_id is ConditionId.B2 else (),
        )
        identity = RequestIdentity.from_request(
            experiment_id=plan.experiment_id,
            trace_sha256=plan.trace_sha256,
            diagnosis_sha256=plan.diagnosis_sha256,
            condition_id=plan.condition_id.value,
            prompt_version=plan.prompt_version,
            prompt_sha256=prepared.prompt_sha256,
            provider=plan.provider,
            model=plan.model,
            messages=prepared.messages,
            generation=prepared.generation,
        )
        try:
            delegate = {
                "deepseek": self._providers.deepseek,
                "qwen": self._providers.qwen,
            }[plan.provider]
            pricing = self._providers.pricing[plan.provider]
        except KeyError as error:
            raise ProviderConfigurationError("provider composition is missing") from error
        return GuardedProvider(
            delegate=delegate,
            cache=self._cache,
            ledger=self._ledger,
            pricing=pricing,
            authorization=self._authorization,
            mode=self._config.mode,
            identity=identity,
        )

    async def execute(self, plan: ConditionPlan) -> ConditionResult:
        candidate = self._candidates.get(plan.cell)
        if candidate is None or candidate.report_sha256 != plan.diagnosis_sha256:
            raise ValueError("condition plan has no verified diagnosis candidate")
        verification_input = self._verification_input(candidate)
        shared_verifier_messages = reconstruct_shared_verifier_messages(
            candidate, _VERIFIER_INSTRUCTION
        )
        diagnosis_messages = shared_verifier_messages[:-1]
        context = ConditionExecutionContext(
            plan=plan,
            verification_input=verification_input,
            diagnosis_messages=diagnosis_messages,
        )
        if plan.condition_id in {ConditionId.B0, ConditionId.B1}:
            unavailable = cast(GuardedProvider, object())
            return await self._conditions.execute(
                context,
                deterministic=self._deterministic(candidate),
                deepseek=unavailable,
                qwen=unavailable,
            )
        guarded = self._guarded_provider(plan, verification_input, diagnosis_messages)
        return await self._conditions.execute(
            context,
            deterministic=self._deterministic(candidate),
            deepseek=guarded,
            qwen=guarded,
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
        authorization = PaidRunAuthorization(
            experiment_id=config.experiment_id,
            allow_live_provider=request.allow_live_provider,
            formal_run=request.formal_run,
            frozen_manifest_sha256=matrix_manifest_sha256,
        )
        authorization.require(config.mode)
        providers = _compose_live_providers(config, environ=os.environ)
        state_path = (
            Path(".cache/phase5")
            / f"{config.experiment_id}-{canonical_sha256(matrix)[:16]}.sqlite3"
        )
        executor = _LiveConditionExecutor(
            candidates=candidates,
            config=config,
            providers=providers,
            authorization=authorization,
            database_path=state_path,
        )
        asyncio.run(
            ExperimentRunner(executor=executor).run_provider_phase(
                plans=plans,
                matrix=matrix,
                repository=ProviderPhaseRepository(request.output_dir),
            )
        )
        return
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
