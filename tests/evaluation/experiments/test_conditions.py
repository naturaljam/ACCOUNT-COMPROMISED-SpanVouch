import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from spanvouch.contracts.diagnosis import ProviderUsage
from spanvouch.contracts.verification import (
    VerificationInput,
    VerifierKind,
    VerifierProvenance,
    VerifierReport,
    VerifierVerdict,
)
from spanvouch.contracts.versioning import canonical_json, canonical_sha256
from spanvouch.diagnosis.errors import ProviderRequestError
from spanvouch.diagnosis.protocols import ChatMessage, GenerationConfig, ProviderResponse
from spanvouch.evaluation.corpus import CorpusCell
from spanvouch.evaluation.experiments.conditions import (
    ConditionExecutionContext,
    ConditionExecutor,
)
from spanvouch.evaluation.experiments.config import (
    ConditionId,
    ModelEndpointConfig,
    load_experiment_config,
)
from spanvouch.evaluation.experiments.models import (
    ConditionPlan,
    ConditionStatus,
    ExperimentFailureCategory,
    ProviderPlanStatus,
    SelectiveAction,
)
from spanvouch.evaluation.experiments.provider import (
    GuardedProviderResult,
    ProviderRequestAudit,
    RequestIdentity,
)
from spanvouch.labs.runtime import FrameworkId
from spanvouch.trace.evidence_catalog import EvidenceCatalog
from spanvouch.verification.prompting import SemanticPromptBuilder
from tests.review.factories import make_diagnosis_report, make_review_snapshot


def _input() -> VerificationInput:
    report = make_diagnosis_report()
    return VerificationInput(
        snapshot=make_review_snapshot(),
        report=report,
        report_sha256=canonical_sha256(report),
    )


def _endpoint(condition: ConditionId) -> ModelEndpointConfig | None:
    config = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    if condition in {ConditionId.B0, ConditionId.B1}:
        return None
    if condition is ConditionId.B2:
        return config.shared_verifier
    if condition is ConditionId.B3:
        return config.isolated_verifier
    return config.cross_model_verifier


def _plan(condition: ConditionId, input_: VerificationInput) -> ConditionPlan:
    endpoint = _endpoint(condition)
    prompt = {
        ConditionId.B0: "phase5-no-verifier-v1",
        ConditionId.B1: "phase5-deterministic-v1",
    }.get(condition, endpoint.prompt_version if endpoint is not None else "unused")
    return ConditionPlan.from_payload(
        experiment_id="phase5-fixture",
        experiment_config_sha256="1" * 64,
        corpus_manifest_sha256="2" * 64,
        cell=CorpusCell(
            domain="supportlab",
            template_id="template-1",
            scenario_id="scenario-1",
            framework_id=FrameworkId.LANGGRAPH,
            repetition=1,
            seed=20260719,
        ),
        record_sha256="3" * 64,
        trace_sha256="4" * 64,
        diagnosis_sha256=input_.report_sha256,
        condition_id=condition,
        prompt_version=prompt,
        provider_status=(
            ProviderPlanStatus.NOT_REQUIRED
            if endpoint is None
            else ProviderPlanStatus.REQUIRED
        ),
        provider=endpoint.provider if endpoint is not None else None,
        model=endpoint.model if endpoint is not None else None,
        generation=endpoint,
    )


def _context(condition: ConditionId) -> ConditionExecutionContext:
    input_ = _input()
    return ConditionExecutionContext(
        plan=_plan(condition, input_),
        verification_input=input_,
        diagnosis_messages=(
            ChatMessage(role="system", content="Diagnosis system."),
            ChatMessage(role="user", content="Diagnosis input."),
            ChatMessage(role="assistant", content=canonical_json(input_.report)),
        ),
    )


def _report(input_: VerificationInput, verdict: VerifierVerdict) -> VerifierReport:
    now = datetime(2026, 7, 20, tzinfo=UTC)
    return VerifierReport(
        verifier_run_id=f"deterministic-{verdict.value}",
        revision_number=input_.revision_number,
        report_sha256=input_.report_sha256,
        verifier_kind=VerifierKind.DETERMINISTIC,
        verdict=verdict,
        provenance=VerifierProvenance(
            verifier_kind=VerifierKind.DETERMINISTIC,
            verifier_version="fixture-v1",
            policy_version="fixture-policy-v1",
        ),
        started_at=now,
        completed_at=now,
    )


class _RecordingVerifier:
    kind = VerifierKind.DETERMINISTIC
    version_fingerprint = "fixture-v1"

    def __init__(self, verdict: VerifierVerdict) -> None:
        self.verdict = verdict
        self.calls = 0

    async def verify(self, request: VerificationInput) -> VerifierReport:
        self.calls += 1
        return _report(request, self.verdict)


class _GuardedFake:
    def __init__(
        self,
        identity: RequestIdentity,
        *,
        content: str,
        error: Exception | None = None,
    ) -> None:
        self.identity = identity
        self.content = content
        self.error = error
        self.calls: list[tuple[tuple[ChatMessage, ...], GenerationConfig]] = []

    async def complete(
        self, messages: tuple[ChatMessage, ...], generation: GenerationConfig
    ) -> GuardedProviderResult:
        self.calls.append((messages, generation))
        if self.error is not None:
            raise self.error
        rebuilt = RequestIdentity.from_request(
            experiment_id=self.identity.experiment_id,
            trace_sha256=self.identity.trace_sha256,
            diagnosis_sha256=self.identity.diagnosis_sha256,
            condition_id=self.identity.condition_id,
            prompt_version=self.identity.prompt_version,
            prompt_sha256=self.identity.prompt_sha256,
            provider=self.identity.provider,
            model=self.identity.model,
            messages=messages,
            generation=generation,
        )
        assert rebuilt == self.identity
        now = datetime(2026, 7, 20, tzinfo=UTC)
        usage = ProviderUsage(
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            latency_ms=1.0,
            request_id=None,
        )
        return GuardedProviderResult(
            response=ProviderResponse(
                content=self.content,
                model=generation.model,
                response_id="sha256-response",
                finish_reason="stop",
                usage=usage,
            ),
            cache_hit=False,
            original_usage=usage,
            cost_cny=Decimal("0.01"),
            audit=ProviderRequestAudit(
                request_sha256=self.identity.sha256,
                provider=self.identity.provider,
                model=self.identity.model,
                field_names=tuple(RequestIdentity.model_fields),
                started_at_utc=now,
                completed_at_utc=now,
                status="completed",
                leakage_scan_passed=True,
            ),
        )


def _semantic_provider(
    context: ConditionExecutionContext,
    *,
    verdict: str = "verified",
    error: Exception | None = None,
) -> _GuardedFake:
    plan = context.plan
    assert plan.generation is not None and plan.provider is not None and plan.model is not None
    generation = GenerationConfig(
        model=plan.model,
        max_tokens=plan.generation.max_tokens,
        temperature=plan.generation.temperature,
        extra_body=plan.generation.extra_body,
    )
    catalog = EvidenceCatalog.from_view(context.verification_input.snapshot.trace_view())
    builder = SemanticPromptBuilder(prompt_version=plan.prompt_version)
    prepared = (
        builder.shared(
            context.diagnosis_messages,
            context.verification_input,
            catalog,
            generation,
        )
        if plan.condition_id is ConditionId.B2
        else builder.isolated(context.verification_input, catalog, generation)
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
    return _GuardedFake(
        identity,
        content=json.dumps(
            {
                "verdict": verdict,
                "findings": [],
                "evidence_gaps": [],
                "alternative_failure_type": None,
                "confidence": 0.9,
            }
        ),
        error=error,
    )


@pytest.mark.asyncio
async def test_b0_accepts_contract_valid_diagnosis_without_any_call() -> None:
    context = _context(ConditionId.B0)
    deterministic = _RecordingVerifier(VerifierVerdict.REVIEW_REQUIRED)
    never = object()

    result = await ConditionExecutor().execute(
        context, deterministic=deterministic, deepseek=never, qwen=never
    )

    assert result.selective_action is SelectiveAction.ACCEPT
    assert result.status is ConditionStatus.COMPLETED
    assert result.diagnosis_sha256 == context.verification_input.report_sha256
    assert deterministic.calls == 0


@pytest.mark.parametrize(
    ("verdict", "action"),
    [
        (VerifierVerdict.VERIFIED, SelectiveAction.ACCEPT),
        (VerifierVerdict.NEEDS_EVIDENCE, SelectiveAction.REVIEW),
        (VerifierVerdict.REVIEW_REQUIRED, SelectiveAction.REVIEW),
    ],
)
@pytest.mark.asyncio
async def test_b1_accepts_only_deterministic_verified(
    verdict: VerifierVerdict, action: SelectiveAction
) -> None:
    context = _context(ConditionId.B1)
    deterministic = _RecordingVerifier(verdict)
    result = await ConditionExecutor().execute(
        context, deterministic=deterministic, deepseek=object(), qwen=object()
    )
    assert result.selective_action is action
    assert deterministic.calls == 1


@pytest.mark.asyncio
async def test_condition_result_carries_only_hash_bound_sanitized_evaluation_evidence() -> None:
    context = _context(ConditionId.B1)
    result = await ConditionExecutor().execute(
        context,
        deterministic=_RecordingVerifier(VerifierVerdict.VERIFIED),
        deepseek=object(),
        qwen=object(),
    )

    evidence = result.evaluation_evidence
    assert evidence is not None
    assert evidence.diagnosis_report_sha256 == context.verification_input.report_sha256
    assert evidence.diagnosis_family == "policy_violation"
    assert evidence.causal_stages == ("cause",)
    assert {"refund", "tool", "rejected", "request"} <= set(evidence.causal_tokens)
    assert evidence.diagnosis_selectors == (
        "span-tool::attributes.tool.error.type",
    )
    assert evidence.verifier_reports[0].verdict is VerifierVerdict.VERIFIED
    assert evidence.verifier_reports[0].artifact_sha256 == result.verifier_report_sha256s[0]
    serialized = evidence.model_dump_json().casefold()
    assert "the refund tool rejected the request" not in serialized
    assert all(term not in serialized for term in ("prompt", "message", "response", "body"))

    tampered = result.model_dump(mode="python")
    tampered["evaluation_evidence"]["diagnosis_family"] = "wrong_tool"
    with pytest.raises(ValidationError, match="projection hash mismatch"):
        type(result).model_validate(tampered)


def test_condition_context_rejects_diagnosis_hash_and_message_history_drift() -> None:
    context = _context(ConditionId.B0)
    plan_payload = context.plan.model_dump(mode="python", exclude={"plan_id"})
    drifted_plan = ConditionPlan.from_payload(
        **{**plan_payload, "diagnosis_sha256": "f" * 64}
    )
    with pytest.raises(ValidationError, match="plan diagnosis"):
        ConditionExecutionContext(
            plan=drifted_plan,
            verification_input=context.verification_input,
            diagnosis_messages=context.diagnosis_messages,
        )
    with pytest.raises(ValidationError, match="history"):
        ConditionExecutionContext(
            plan=context.plan,
            verification_input=context.verification_input,
            diagnosis_messages=context.diagnosis_messages[:-1],
        )


@pytest.mark.asyncio
async def test_executor_converts_invalid_copied_context_to_typed_failure() -> None:
    context = _context(ConditionId.B0).model_copy(update={"diagnosis_messages": ()})
    result = await ConditionExecutor().execute(
        context,
        deterministic=_RecordingVerifier(VerifierVerdict.VERIFIED),
        deepseek=object(),
        qwen=object(),
    )
    assert result.status is ConditionStatus.FAILED
    assert result.failure is not None
    assert result.failure.category is ExperimentFailureCategory.CONTRACT_INVALID


@pytest.mark.parametrize("condition", [ConditionId.B2, ConditionId.B3, ConditionId.B4])
@pytest.mark.asyncio
async def test_semantic_conditions_call_the_selected_provider_and_accept_verified(
    condition: ConditionId,
) -> None:
    context = _context(condition)
    provider = _semantic_provider(context)
    other = object()
    result = await ConditionExecutor().execute(
        context,
        deterministic=_RecordingVerifier(VerifierVerdict.VERIFIED),
        deepseek=provider if condition in {ConditionId.B2, ConditionId.B3} else other,
        qwen=provider if condition is ConditionId.B4 else other,
    )
    assert result.selective_action is SelectiveAction.ACCEPT
    assert len(provider.calls) == 1
    assert len(result.verifier_report_sha256s) == 1
    assert len(result.request_audit_sha256s) == 1


@pytest.mark.asyncio
async def test_b2_b3_only_request_difference_is_shared_diagnosis_history() -> None:
    b2 = _context(ConditionId.B2)
    b3 = _context(ConditionId.B3)
    deepseek2 = _semantic_provider(b2)
    deepseek3 = _semantic_provider(b3)
    executor = ConditionExecutor()
    await executor.execute(
        b2,
        deterministic=_RecordingVerifier(VerifierVerdict.VERIFIED),
        deepseek=deepseek2,
        qwen=object(),
    )
    await executor.execute(
        b3,
        deterministic=_RecordingVerifier(VerifierVerdict.VERIFIED),
        deepseek=deepseek3,
        qwen=object(),
    )
    shared_messages, shared_generation = deepseek2.calls[0]
    isolated_messages, isolated_generation = deepseek3.calls[0]

    assert shared_messages[:3] == b2.diagnosis_messages
    assert shared_messages[3:] == isolated_messages
    assert shared_generation == isolated_generation
    assert b2.verification_input.report_sha256 == b3.verification_input.report_sha256


@pytest.mark.asyncio
async def test_b3_b4_use_identical_evidence_view_without_deepseek_context() -> None:
    b3 = _context(ConditionId.B3)
    b4 = _context(ConditionId.B4)
    deepseek = _semantic_provider(b3)
    qwen = _semantic_provider(b4)
    executor = ConditionExecutor()
    await executor.execute(
        b3,
        deterministic=_RecordingVerifier(VerifierVerdict.VERIFIED),
        deepseek=deepseek,
        qwen=object(),
    )
    await executor.execute(
        b4,
        deterministic=_RecordingVerifier(VerifierVerdict.VERIFIED),
        deepseek=object(),
        qwen=qwen,
    )

    deepseek_messages, _ = deepseek.calls[0]
    qwen_messages, _ = qwen.calls[0]
    assert deepseek_messages == qwen_messages
    assert all(message not in qwen_messages for message in b4.diagnosis_messages)


@pytest.mark.asyncio
async def test_b5_skips_qwen_when_deterministic_does_not_pass() -> None:
    context = _context(ConditionId.B5)
    qwen = _semantic_provider(context)
    result = await ConditionExecutor().execute(
        context,
        deterministic=_RecordingVerifier(VerifierVerdict.REVIEW_REQUIRED),
        deepseek=object(),
        qwen=qwen,
    )
    assert result.status is ConditionStatus.NOT_INVOKED_BY_POLICY
    assert result.selective_action is SelectiveAction.REVIEW
    assert result.cache_status == "not_invoked_by_policy"
    assert qwen.calls == []


@pytest.mark.asyncio
async def test_b5_calls_qwen_only_after_deterministic_pass() -> None:
    context = _context(ConditionId.B5)
    qwen = _semantic_provider(context)
    result = await ConditionExecutor().execute(
        context,
        deterministic=_RecordingVerifier(VerifierVerdict.VERIFIED),
        deepseek=object(),
        qwen=qwen,
    )
    assert result.selective_action is SelectiveAction.ACCEPT
    assert len(qwen.calls) == 1
    assert len(result.verifier_report_sha256s) == 2


@pytest.mark.asyncio
async def test_nonverified_and_provider_failure_never_silently_accept() -> None:
    review_context = _context(ConditionId.B3)
    review_provider = _semantic_provider(review_context, verdict="review_required")
    review = await ConditionExecutor().execute(
        review_context,
        deterministic=_RecordingVerifier(VerifierVerdict.VERIFIED),
        deepseek=review_provider,
        qwen=object(),
    )
    assert review.selective_action is SelectiveAction.REVIEW

    failed_context = _context(ConditionId.B4)
    failed_provider = _semantic_provider(
        failed_context,
        error=ProviderRequestError("transport_error", retryable=True),
    )
    failed = await ConditionExecutor().execute(
        failed_context,
        deterministic=_RecordingVerifier(VerifierVerdict.VERIFIED),
        deepseek=object(),
        qwen=failed_provider,
    )
    assert failed.selective_action is SelectiveAction.REVIEW
    assert failed.status is ConditionStatus.FAILED
    assert failed.failure is not None
    assert failed.failure.category is ExperimentFailureCategory.PROVIDER


@pytest.mark.asyncio
async def test_request_identity_drift_fails_closed_before_provider_call() -> None:
    context = _context(ConditionId.B3)
    provider = _semantic_provider(context)
    provider.identity = provider.identity.model_copy(update={"trace_sha256": "f" * 64})

    result = await ConditionExecutor().execute(
        context,
        deterministic=_RecordingVerifier(VerifierVerdict.VERIFIED),
        deepseek=provider,
        qwen=object(),
    )

    assert result.status is ConditionStatus.FAILED
    assert result.selective_action is SelectiveAction.REVIEW
    assert result.failure is not None
    assert result.failure.category is ExperimentFailureCategory.CONTRACT_INVALID
    assert provider.calls == []


def test_executor_surface_contains_no_label_input() -> None:
    import inspect

    signature = str(inspect.signature(ConditionExecutor.execute)).lower()
    assert "label" not in signature
    assert "gold" not in signature
    assert "split" not in signature
