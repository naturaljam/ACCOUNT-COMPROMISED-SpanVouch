import json

import pytest

from spanvouch.contracts.diagnosis import AbstainReason, DiagnosisStatus, ProviderUsage
from spanvouch.contracts.trace import DiagnosticContext
from spanvouch.diagnosis.llm_diagnoser import LlmDiagnoser
from spanvouch.diagnosis.protocols import (
    ChatMessage,
    GenerationConfig,
    ProviderResponse,
)
from spanvouch.failure_types import FailureType
from spanvouch.trace.diagnostic_view import TraceProjector
from spanvouch.trace.evidence_catalog import EvidenceCatalog
from tests.trace.test_diagnostic_view import load_trace


class RecordingProvider:
    def __init__(self, content: str, *, finish_reason: str = "stop") -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.messages: tuple[ChatMessage, ...] = ()

    async def complete(
        self,
        messages: tuple[ChatMessage, ...],
        config: GenerationConfig,
    ) -> ProviderResponse:
        self.messages = messages
        return ProviderResponse(
            content=self.content,
            model=config.model,
            response_id="response-1",
            finish_reason=self.finish_reason,
            usage=ProviderUsage(
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                latency_ms=10.0,
                request_id="response-1",
            ),
        )


def inputs() -> tuple[DiagnosticContext, EvidenceCatalog]:
    context = TraceProjector().project(load_trace("invalid_argument-01"))
    return context, EvidenceCatalog.from_context(context)


@pytest.mark.asyncio
async def test_prompt_excludes_identity_labels_and_invariant_results() -> None:
    provider = RecordingProvider(
        json.dumps(
            {
                "status": "no_failure",
                "failure_type": "no_failure",
                "critical_span_ids": [],
                "causal_chain": [],
                "confidence": 0.5,
                "abstain_reason": None,
            }
        )
    )
    view, evidence = inputs()

    await LlmDiagnoser(provider).diagnose(view, evidence)

    prompt = "\n".join(message.content for message in provider.messages)
    for forbidden in (
        "invalid_argument-01",
        "scenario.expected_failure",
        "idempotency_key",
        "ignore_error",
        "calculated_amount",
        "supportlab-trace-",
        "InvariantResult",
    ):
        assert forbidden not in prompt
    assert "unsupported_failure_type" in prompt
    assert "span-005::attributes.tool.error.message" in prompt


@pytest.mark.asyncio
async def test_prompt_defines_exact_json_enum_and_scalar_contract() -> None:
    provider = RecordingProvider(
        json.dumps(
            {
                "status": "no_failure",
                "failure_type": "no_failure",
                "critical_span_ids": [],
                "causal_chain": [],
                "confidence": 0.5,
                "abstain_reason": None,
            }
        )
    )
    view, evidence = inputs()

    await LlmDiagnoser(provider).diagnose(view, evidence)

    system = provider.messages[0].content
    assert "status must be exactly one of: diagnosed, no_failure, abstained" in system
    assert "stage must be exactly one of: cause, propagation, outcome" in system
    assert "confidence must be a JSON number from 0.0 to 1.0" in system
    assert "Do not use words such as failure, high, medium, or low" in system


@pytest.mark.asyncio
async def test_valid_draft_resolves_selector_from_local_catalog() -> None:
    selector = "span-005::attributes.tool.error.message"
    provider = RecordingProvider(
        json.dumps(
            {
                "status": "diagnosed",
                "failure_type": "invalid_argument",
                "critical_span_ids": ["span-005"],
                "causal_chain": [
                    {
                        "stage": "cause",
                        "statement": "The submitted amount was rejected.",
                        "evidence_selectors": [selector],
                    }
                ],
                "confidence": 0.9,
                "abstain_reason": None,
            }
        )
    )
    view, evidence = inputs()

    execution = await LlmDiagnoser(provider).diagnose(view, evidence)

    assert execution.decision.status is DiagnosisStatus.DIAGNOSED
    assert execution.decision.failure_type == FailureType.INVALID_ARGUMENT
    assert execution.decision.evidence[0].observed_value == (
        "amount_exceeds_calculation,amount_exceeds_policy"
    )
    assert execution.decision.evidence[0].canonical == selector
    assert execution.provenance.prompt_sha256
    assert execution.usage is not None
    assert execution.usage.total_tokens == 120


@pytest.mark.parametrize(
    ("content", "finish_reason"),
    [("", "stop"), ("not-json", "stop"), ("{}", "stop"), ("{}", "length")],
)
@pytest.mark.asyncio
async def test_invalid_model_output_becomes_semantic_abstain(
    content: str, finish_reason: str
) -> None:
    provider = RecordingProvider(content, finish_reason=finish_reason)
    view, evidence = inputs()

    execution = await LlmDiagnoser(provider).diagnose(view, evidence)

    assert execution.decision.status is DiagnosisStatus.ABSTAINED
    assert execution.decision.abstain_reason is AbstainReason.INVALID_MODEL_OUTPUT


@pytest.mark.asyncio
async def test_nonexistent_selector_becomes_evidence_abstain() -> None:
    provider = RecordingProvider(
        json.dumps(
            {
                "status": "diagnosed",
                "failure_type": "invalid_argument",
                "critical_span_ids": ["span-999"],
                "causal_chain": [
                    {
                        "stage": "cause",
                        "statement": "Invented evidence.",
                        "evidence_selectors": ["span-999::attributes.fake"],
                    }
                ],
                "confidence": 1.0,
                "abstain_reason": None,
            }
        )
    )
    view, evidence = inputs()

    execution = await LlmDiagnoser(provider).diagnose(view, evidence)

    assert execution.decision.status is DiagnosisStatus.ABSTAINED
    assert execution.decision.abstain_reason is AbstainReason.INVALID_EVIDENCE_REFERENCE
