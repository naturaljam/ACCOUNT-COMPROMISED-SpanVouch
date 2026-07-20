"""Explicit infrastructure composition root for live Phase 5 providers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx
from pydantic import SecretStr

from spanvouch.adapters.models.deepseek import DeepSeekConfig, DeepSeekProvider
from spanvouch.adapters.models.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from spanvouch.contracts.verification import ReviewInputSnapshot, VerificationInput
from spanvouch.contracts.versioning import canonical_json, canonical_sha256
from spanvouch.diagnosis.protocols import ChatMessage, ModelProvider
from spanvouch.evaluation.experiments.budget import BudgetLedger, Pricing
from spanvouch.evaluation.experiments.conditions import (
    ConditionExecutionContext,
    ConditionExecutor,
)
from spanvouch.evaluation.experiments.config import ConditionId, Phase5ExperimentConfig
from spanvouch.evaluation.experiments.diagnosis import (
    FrozenDiagnosisCandidate,
    reconstruct_shared_verifier_messages,
)
from spanvouch.evaluation.experiments.models import ConditionPlan, ConditionResult
from spanvouch.evaluation.experiments.provider import (
    GuardedProvider,
    PaidRunAuthorization,
    ProviderConfigurationError,
    ProviderResultCache,
    RequestIdentity,
)
from spanvouch.labs.opslab.invariants import opslab_rules
from spanvouch.labs.supportlab.invariants import supportlab_rules
from spanvouch.verification.deterministic import DeterministicVerifier
from spanvouch.verification.invariant_engine import InvariantEngine

_VERIFIER_INSTRUCTION = "Critique evidence sufficiency only."
_POLICY_VERSION = "phase5-deterministic-v1"


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
            experiment_config_sha256=plan.experiment_config_sha256,
            deployment_provenance_sha256={
                "deepseek": self._config.live_provenance.deepseek.sha256,
                "qwen": self._config.live_provenance.qwen.sha256,
            }[plan.provider],
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


def compose_live_executor(
    *,
    candidates: tuple[FrozenDiagnosisCandidate, ...],
    config: Phase5ExperimentConfig,
    authorization: PaidRunAuthorization,
    matrix_manifest_sha256: str,
    environ: Mapping[str, str] | None = None,
    deepseek_client: httpx.AsyncClient | None = None,
    qwen_client: httpx.AsyncClient | None = None,
) -> _LiveConditionExecutor:
    """Build the only live provider executor from validated experiment parents."""
    providers = _compose_live_providers(
        config,
        environ=os.environ if environ is None else environ,
        deepseek_client=deepseek_client,
        qwen_client=qwen_client,
    )
    state_path = (
        Path(".cache/phase5")
        / f"{config.experiment_id}-{matrix_manifest_sha256[:16]}.sqlite3"
    )
    return _LiveConditionExecutor(
        candidates=candidates,
        config=config,
        providers=providers,
        authorization=authorization,
        database_path=state_path,
    )
