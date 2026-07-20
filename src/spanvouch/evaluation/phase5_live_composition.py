"""Explicit infrastructure composition root for live Phase 5 providers."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import SecretStr

from spanvouch.adapters.models.deepseek import DeepSeekConfig, DeepSeekProvider
from spanvouch.adapters.models.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from spanvouch.contracts.verification import ReviewInputSnapshot, VerificationInput
from spanvouch.contracts.versioning import (
    canonical_bytes,
    canonical_json,
    canonical_sha256,
)
from spanvouch.diagnosis.protocols import ChatMessage, ModelProvider
from spanvouch.evaluation.experiments.budget import BudgetLedger, Pricing
from spanvouch.evaluation.experiments.conditions import (
    ConditionExecutionContext,
    ConditionExecutor,
)
from spanvouch.evaluation.experiments.config import (
    ConditionId,
    EndpointDeploymentProvenance,
    ExperimentMode,
    Phase5ExperimentConfig,
)
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


def _require_live_composition_authorization(
    config: Phase5ExperimentConfig,
    authorization: PaidRunAuthorization,
    matrix_manifest_sha256: str,
) -> tuple[Phase5ExperimentConfig, PaidRunAuthorization]:
    """Reconstruct and bind authorization before any live infrastructure access."""
    try:
        validated_config = Phase5ExperimentConfig.model_validate(
            config.model_dump(mode="python")
        )
        validated_authorization = PaidRunAuthorization.model_validate(
            authorization.model_dump(mode="python")
        )
    except ValueError:
        raise ProviderConfigurationError(
            "live composition authorization is invalid"
        ) from None
    expected_formal_run = validated_config.mode is ExperimentMode.FORMAL
    if (
        not validated_authorization.allow_live_provider
        or validated_authorization.experiment_id != validated_config.experiment_id
        or validated_authorization.formal_run != expected_formal_run
        or validated_authorization.frozen_manifest_sha256 is None
        or validated_authorization.frozen_manifest_sha256 != matrix_manifest_sha256
    ):
        raise ProviderConfigurationError("live composition authorization is invalid")
    return validated_config, validated_authorization


def _required_environment(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ProviderConfigurationError(f"{name} is not configured")
    return value


def _normalized_base_url(value: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        port = parsed.port
    except ValueError as error:
        raise ProviderConfigurationError("provider base URL is invalid") from error
    default_port = (parsed.scheme == "https" and port == 443) or (
        parsed.scheme == "http" and port == 80
    )
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    authority = host if port is None or default_port else f"{host}:{port}"
    normalized = urlunsplit(
        (parsed.scheme.lower(), authority, parsed.path.rstrip("/"), "", "")
    )
    return normalized, sha256(normalized.encode("utf-8")).hexdigest()


def _load_pricing(path: str, provenance: EndpointDeploymentProvenance) -> Pricing:
    try:
        content = Path(path).read_bytes()
        payload = json.loads(content)
        if canonical_bytes(payload) != content:
            raise ValueError("pricing file is not canonical")
        pricing = Pricing.model_validate(payload)
        pricing.require_endpoint(provenance.provider, provenance.model)
        if (
            sha256(content).hexdigest() != provenance.pricing.canonical_sha256
            or pricing.source_url != provenance.pricing.source_url
            or pricing.effective_date != provenance.pricing.effective_date
            or pricing.currency != provenance.pricing.currency
            or pricing.provider != provenance.provider
            or pricing.model != provenance.model
        ):
            raise ValueError("pricing identity drift")
    except (OSError, ValueError) as error:
        raise ProviderConfigurationError(
            "provider pricing provenance is missing or invalid"
        ) from error
    return pricing


@dataclass(frozen=True)
class _LiveProviderComposition:
    deepseek: ModelProvider
    qwen: ModelProvider
    pricing: Mapping[str, Pricing]


@dataclass(frozen=True)
class LiveDiagnosisDependencies:
    """Approved DeepSeek generation dependencies sharing the live guard stack."""

    provider: ModelProvider
    pricing: Pricing
    cache: ProviderResultCache
    ledger: BudgetLedger
    authorization: PaidRunAuthorization


def _compose_deepseek_endpoint(
    config: Phase5ExperimentConfig,
    *,
    environ: Mapping[str, str],
    client: httpx.AsyncClient | None = None,
) -> tuple[ModelProvider, Pricing]:
    provenance = config.live_provenance.deepseek
    base_url, base_url_sha256 = _normalized_base_url(
        _required_environment(environ, "SPANVOUCH_DEEPSEEK_BASE_URL")
    )
    if base_url_sha256 != provenance.base_url_sha256:
        raise ProviderConfigurationError("provider provenance mismatch")
    pricing = _load_pricing(
        _required_environment(environ, "SPANVOUCH_PHASE5_DEEPSEEK_PRICING_PATH"),
        provenance,
    )
    key = _required_environment(environ, "DEEPSEEK_API_KEY")
    return (
        DeepSeekProvider(
            DeepSeekConfig(api_key=SecretStr(key), base_url=base_url),
            client=client,
        ),
        pricing,
    )


def compose_live_diagnosis_dependencies(
    config: Phase5ExperimentConfig,
    *,
    authorization: PaidRunAuthorization,
    generation_manifest_sha256: str,
    state_path: Path,
    environ: Mapping[str, str] | None = None,
    deepseek_client: httpx.AsyncClient | None = None,
) -> LiveDiagnosisDependencies:
    """Authorize exact generation identity before credentials or persistent state."""
    config, authorization = _require_live_composition_authorization(
        config, authorization, generation_manifest_sha256
    )
    if config.generator.provider != "deepseek" or (
        config.generator.model != config.live_provenance.deepseek.model
    ):
        raise ProviderConfigurationError("diagnosis generator provenance mismatch")
    provider, pricing = _compose_deepseek_endpoint(
        config,
        environ=os.environ if environ is None else environ,
        client=deepseek_client,
    )
    return LiveDiagnosisDependencies(
        provider=provider,
        pricing=pricing,
        cache=ProviderResultCache(state_path),
        ledger=BudgetLedger(state_path, config.budget),
        authorization=authorization,
    )


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
    qwen_provenance = config.live_provenance.qwen
    qwen_base_url, qwen_base_url_sha256 = _normalized_base_url(
        _required_environment(environ, "SPANVOUCH_VLLM_BASE_URL")
    )
    qwen_repo_digest = _required_environment(
        environ, "SPANVOUCH_VLLM_CONTAINER_REPO_DIGEST"
    )
    qwen_hf_revision = _required_environment(environ, "SPANVOUCH_VLLM_HF_REVISION")
    try:
        qwen_max_model_len = int(
            _required_environment(environ, "SPANVOUCH_VLLM_MAX_MODEL_LEN")
        )
    except ValueError as error:
        raise ProviderConfigurationError("provider provenance mismatch") from error
    if (
        qwen_base_url_sha256 != qwen_provenance.base_url_sha256
        or qwen_repo_digest != qwen_provenance.container_repo_digest
        or qwen_hf_revision != qwen_provenance.hf_revision
        or _required_environment(environ, "SPANVOUCH_VLLM_CHAT_TEMPLATE_SHA256")
        != qwen_provenance.chat_template_sha256
        or _required_environment(environ, "SPANVOUCH_VLLM_DTYPE")
        != qwen_provenance.dtype
        or qwen_max_model_len != qwen_provenance.max_model_len
    ):
        raise ProviderConfigurationError("provider provenance mismatch")
    deepseek_provider, deepseek_pricing = _compose_deepseek_endpoint(
        config, environ=environ, client=deepseek_client
    )
    qwen_pricing = _load_pricing(
        _required_environment(environ, "SPANVOUCH_PHASE5_QWEN_PRICING_PATH"),
        qwen_provenance,
    )
    qwen_key = _required_environment(environ, "SPANVOUCH_VLLM_API_KEY")
    qwen_config = OpenAICompatibleConfig(
        api_key=SecretStr(qwen_key),
        base_url=qwen_base_url,
        expected_model=qwen_endpoint.model,
        endpoint_class=qwen_endpoint.endpoint_class,
        smoke_only=False,
        container_repo_digest=qwen_repo_digest,
        hf_revision=qwen_hf_revision,
    ).validate_for_experiment(config.mode.value)
    return _LiveProviderComposition(
        deepseek=deepseek_provider,
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
    config, authorization = _require_live_composition_authorization(
        config, authorization, matrix_manifest_sha256
    )
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
