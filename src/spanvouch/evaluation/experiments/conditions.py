"""Execution of the preregistered Phase 5 B0-B5 verification conditions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, model_validator

from spanvouch.contracts.diagnosis import ProviderUsage
from spanvouch.contracts.verification import (
    VerificationInput,
    VerifierReport,
    VerifierVerdict,
)
from spanvouch.contracts.versioning import canonical_json, canonical_sha256
from spanvouch.diagnosis.protocols import ChatMessage, GenerationConfig, ProviderResponse
from spanvouch.evaluation.experiments.config import ConditionId
from spanvouch.evaluation.experiments.models import (
    ConditionPlan,
    ConditionResult,
    ConditionStatus,
    ExperimentFailure,
    ExperimentFailureCategory,
    FailureSource,
    SelectiveAction,
)
from spanvouch.evaluation.experiments.provider import (
    GuardedProviderResult,
    RequestIdentity,
)
from spanvouch.trace.evidence_catalog import EvidenceCatalog
from spanvouch.verification.prompting import PreparedVerification, SemanticPromptBuilder
from spanvouch.verification.protocols import Verifier
from spanvouch.verification.semantic import SemanticVerifier


class GuardedProviderLike(Protocol):
    identity: RequestIdentity

    def complete(
        self,
        messages: tuple[ChatMessage, ...],
        generation: GenerationConfig,
    ) -> Awaitable[GuardedProviderResult]: ...


class ConditionExecutionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan: ConditionPlan
    verification_input: VerificationInput
    diagnosis_messages: tuple[ChatMessage, ...]

    @model_validator(mode="after")
    def validate_frozen_inputs(self) -> Self:
        if self.plan.diagnosis_sha256 != self.verification_input.report_sha256:
            raise ValueError("condition plan diagnosis does not match verification input")
        if (
            len(self.diagnosis_messages) != 3
            or tuple(message.role for message in self.diagnosis_messages)
            != ("system", "user", "assistant")
            or self.diagnosis_messages[-1].content
            != canonical_json(self.verification_input.report)
        ):
            raise ValueError("condition diagnosis history does not bind frozen report")
        return self


class _GuardedSemanticAdapter:
    def __init__(self, provider: GuardedProviderLike) -> None:
        self.provider = provider
        self.result: GuardedProviderResult | None = None

    async def complete(
        self,
        messages: tuple[ChatMessage, ...],
        config: GenerationConfig,
    ) -> ProviderResponse:
        if self.result is not None:
            raise RuntimeError("semantic condition attempted multiple provider calls")
        self.result = await self.provider.complete(messages, config)
        return self.result.response


class ConditionExecutor:
    """Execute one label-free condition using fresh verifier state."""

    def __init__(self, at_utc: Callable[[], datetime] | None = None) -> None:
        self._at_utc = at_utc or (lambda: datetime.now(UTC))

    async def execute(
        self,
        context: ConditionExecutionContext,
        *,
        deterministic: Verifier,
        deepseek: GuardedProviderLike,
        qwen: GuardedProviderLike,
    ) -> ConditionResult:
        started = self._at_utc()
        try:
            validated = ConditionExecutionContext.model_validate(
                context.model_dump(mode="python")
            )
        except (TypeError, ValueError):
            return self._failure(
                context.plan,
                started,
                ExperimentFailureCategory.CONTRACT_INVALID,
                "condition-input-invalid",
            )
        plan = validated.plan
        input_ = validated.verification_input
        condition = plan.condition_id
        if condition is ConditionId.B0:
            return self._result(plan, started, SelectiveAction.ACCEPT)
        if condition is ConditionId.B1:
            return await self._deterministic_only(plan, input_, deterministic, started)
        if condition is ConditionId.B2:
            return await self._semantic_only(
                plan,
                input_,
                validated.diagnosis_messages,
                deepseek,
                started,
            )
        if condition is ConditionId.B3:
            return await self._semantic_only(plan, input_, (), deepseek, started)
        if condition is ConditionId.B4:
            return await self._semantic_only(plan, input_, (), qwen, started)
        return await self._deterministic_then_qwen(
            plan, input_, deterministic, qwen, started
        )

    async def _deterministic_only(
        self,
        plan: ConditionPlan,
        input_: VerificationInput,
        verifier: Verifier,
        started: datetime,
    ) -> ConditionResult:
        try:
            report = await self._run_deterministic(verifier, input_)
        except Exception:
            return self._failure(
                plan,
                started,
                ExperimentFailureCategory.CONTRACT_INVALID,
                "deterministic-verifier-invalid",
            )
        return self._result(
            plan,
            started,
            self._action(report),
            verifier_reports=(report,),
        )

    async def _semantic_only(
        self,
        plan: ConditionPlan,
        input_: VerificationInput,
        diagnosis_messages: tuple[ChatMessage, ...],
        provider: GuardedProviderLike,
        started: datetime,
        *,
        prior_reports: tuple[VerifierReport, ...] = (),
    ) -> ConditionResult:
        try:
            prepared = self._prepare(plan, input_, diagnosis_messages)
            self._validate_request_identity(plan, prepared, provider.identity)
            adapter = _GuardedSemanticAdapter(provider)
            semantic = SemanticVerifier(
                adapter,
                provider_id=plan.provider or "invalid",
                model=prepared.generation.model,
                prompt_version=plan.prompt_version,
                generation=prepared.generation,
                diagnosis_messages=diagnosis_messages,
            )
            report = await semantic.verify(input_)
            if report.report_sha256 != plan.diagnosis_sha256:
                raise ValueError("semantic report diagnosis binding mismatch")
            provider_result = adapter.result
            if provider_result is None:
                raise ValueError("semantic provider was not invoked")
        except Exception as error:
            category = (
                ExperimentFailureCategory.CONTRACT_INVALID
                if isinstance(error, (TypeError, ValueError))
                else ExperimentFailureCategory.PROVIDER
            )
            return self._failure(
                plan,
                started,
                category,
                (
                    "semantic-contract-invalid"
                    if category is ExperimentFailureCategory.CONTRACT_INVALID
                    else "semantic-provider-failed"
                ),
            )
        return self._result(
            plan,
            started,
            self._action(report),
            verifier_reports=(*prior_reports, report),
            provider_result=provider_result,
        )

    async def _deterministic_then_qwen(
        self,
        plan: ConditionPlan,
        input_: VerificationInput,
        deterministic: Verifier,
        qwen: GuardedProviderLike,
        started: datetime,
    ) -> ConditionResult:
        try:
            deterministic_report = await self._run_deterministic(deterministic, input_)
        except Exception:
            return self._failure(
                plan,
                started,
                ExperimentFailureCategory.CONTRACT_INVALID,
                "deterministic-verifier-invalid",
            )
        if deterministic_report.verdict is not VerifierVerdict.VERIFIED:
            return self._result(
                plan,
                started,
                SelectiveAction.REVIEW,
                status=ConditionStatus.NOT_INVOKED_BY_POLICY,
                verifier_reports=(deterministic_report,),
                cache_status="not_invoked_by_policy",
            )
        return await self._semantic_only(
            plan,
            input_,
            (),
            qwen,
            started,
            prior_reports=(deterministic_report,),
        )

    @staticmethod
    async def _run_deterministic(
        verifier: Verifier,
        input_: VerificationInput,
    ) -> VerifierReport:
        report = VerifierReport.model_validate(
            (await verifier.verify(input_)).model_dump(mode="python")
        )
        if report.report_sha256 != input_.report_sha256:
            raise ValueError("deterministic report diagnosis binding mismatch")
        return report

    @staticmethod
    def _prepare(
        plan: ConditionPlan,
        input_: VerificationInput,
        diagnosis_messages: tuple[ChatMessage, ...],
    ) -> PreparedVerification:
        endpoint = plan.generation
        if endpoint is None or plan.model is None:
            raise ValueError("semantic condition is missing generation configuration")
        generation = GenerationConfig(
            model=plan.model,
            max_tokens=endpoint.max_tokens,
            temperature=endpoint.temperature,
            extra_body=endpoint.extra_body,
        )
        catalog = EvidenceCatalog.from_view(input_.snapshot.trace_view())
        builder = SemanticPromptBuilder(prompt_version=plan.prompt_version)
        if diagnosis_messages:
            return builder.shared(diagnosis_messages, input_, catalog, generation)
        return builder.isolated(input_, catalog, generation)

    @staticmethod
    def _validate_request_identity(
        plan: ConditionPlan,
        prepared: PreparedVerification,
        identity: RequestIdentity,
    ) -> None:
        if plan.provider is None or plan.model is None:
            raise ValueError("semantic plan has no provider identity")
        rebuilt = RequestIdentity.from_request(
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
        if rebuilt != identity:
            raise ValueError("guarded provider request identity does not match condition plan")

    @staticmethod
    def _action(report: VerifierReport) -> SelectiveAction:
        return (
            SelectiveAction.ACCEPT
            if report.verdict is VerifierVerdict.VERIFIED
            else SelectiveAction.REVIEW
        )

    def _result(
        self,
        plan: ConditionPlan,
        started: datetime,
        action: SelectiveAction,
        *,
        status: ConditionStatus = ConditionStatus.COMPLETED,
        verifier_reports: tuple[VerifierReport, ...] = (),
        provider_result: GuardedProviderResult | None = None,
        cache_status: Literal[
            "not_required", "not_invoked_by_policy", "hit", "miss", "failed"
        ]
        | None = None,
    ) -> ConditionResult:
        usage: ProviderUsage | None = None
        cost: Decimal | None = None
        audit_hashes: tuple[str, ...] = ()
        if provider_result is not None:
            usage = provider_result.original_usage.model_copy(update={"request_id": None})
            cost = provider_result.cost_cny
            audit_hashes = (canonical_sha256(provider_result.audit),)
        return ConditionResult(
            plan_id=plan.plan_id,
            cell=plan.cell,
            record_sha256=plan.record_sha256,
            trace_sha256=plan.trace_sha256,
            diagnosis_sha256=plan.diagnosis_sha256,
            condition_id=plan.condition_id,
            status=status,
            selective_action=action,
            verifier_report_sha256s=tuple(
                canonical_sha256(report) for report in verifier_reports
            ),
            request_audit_sha256s=audit_hashes,
            usage=usage,
            cost_cny=cost,
            cache_status=(
                cache_status
                or (
                    "hit"
                    if provider_result is not None and provider_result.cache_hit
                    else "miss" if provider_result is not None else "not_required"
                )
            ),
            started_at_utc=started,
            completed_at_utc=self._at_utc(),
            failure=None,
        )

    def _failure(
        self,
        plan: ConditionPlan,
        started: datetime,
        category: ExperimentFailureCategory,
        code: str,
    ) -> ConditionResult:
        return ConditionResult(
            plan_id=plan.plan_id,
            cell=plan.cell,
            record_sha256=plan.record_sha256,
            trace_sha256=plan.trace_sha256,
            diagnosis_sha256=plan.diagnosis_sha256,
            condition_id=plan.condition_id,
            status=ConditionStatus.FAILED,
            selective_action=SelectiveAction.REVIEW,
            verifier_report_sha256s=(),
            request_audit_sha256s=(),
            usage=None,
            cost_cny=None,
            cache_status="failed",
            started_at_utc=started,
            completed_at_utc=self._at_utc(),
            failure=ExperimentFailure(
                category=category,
                code=code,
                source=FailureSource.PROVIDER_RUNNER,
            ),
        )
