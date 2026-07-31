import json

import pytest

from spanvouch.contracts.diagnosis import (
    AbstainReason,
    DiagnosisReport,
    DiagnosisStatus,
    ProviderUsage,
)
from spanvouch.contracts.trace import DiagnosticContext
from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.diagnosis.llm_diagnoser import LlmDiagnoser
from spanvouch.diagnosis.protocols import (
    ChatMessage,
    GenerationConfig,
    ProviderResponse,
)
from spanvouch.diagnosis.response_content import ProviderContentDisposition
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


def test_diagnosis_response_policy_accepts_schema_valid_long_statement() -> None:
    from spanvouch.diagnosis.llm_diagnoser import diagnosis_response_content_policy

    normalized = diagnosis_response_content_policy().normalize(
        json.dumps(
            {
                "status": "diagnosed",
                "failure_type": "invalid_final_state",
                "critical_span_ids": ["span-tool"],
                "causal_chain": [
                    {
                        "stage": "cause",
                        "statement": (
                            "degradation_result_remained_missing_after_dependency_call"
                        ),
                        "evidence_selectors": [
                            "span-tool::attributes.tool.error.type"
                        ],
                    }
                ],
                "confidence": 0.8,
                "abstain_reason": None,
            }
        )
    )

    assert normalized.disposition is ProviderContentDisposition.ACCEPTED
    assert "degradation_result_remained_missing_after_dependency_call" in normalized.content


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


@pytest.mark.asyncio
async def test_known_but_unsupported_supportlab_type_becomes_scope_abstain() -> None:
    provider = RecordingProvider(
        json.dumps(
            {
                "status": "diagnosed",
                "failure_type": "missing_precondition",
                "critical_span_ids": ["span-005"],
                "causal_chain": [
                    {
                        "stage": "cause",
                        "statement": "A required lookup was skipped.",
                        "evidence_selectors": ["span-005::name"],
                    }
                ],
                "confidence": 0.9,
                "abstain_reason": None,
            }
        )
    )
    context, evidence = inputs()

    execution = await LlmDiagnoser(provider).diagnose(context, evidence)

    assert execution.decision.status is DiagnosisStatus.ABSTAINED
    assert execution.decision.failure_type is None
    assert execution.decision.critical_span_ids == ()
    assert execution.decision.causal_chain == ()
    assert execution.decision.evidence == ()
    assert execution.decision.confidence == 0.0
    assert (
        execution.decision.abstain_reason
        is AbstainReason.UNSUPPORTED_FAILURE_TYPE
    )


@pytest.mark.asyncio
async def test_future_taxonomy_identifier_is_rejected_at_supportlab_boundary() -> None:
    provider = RecordingProvider(
        json.dumps(
            {
                "status": "diagnosed",
                "failure_type": "opslab.deadlock_cycle",
                "critical_span_ids": ["span-005"],
                "causal_chain": [
                    {
                        "stage": "cause",
                        "statement": "A future taxonomy diagnosis.",
                        "evidence_selectors": ["span-005::name"],
                    }
                ],
                "confidence": 0.9,
                "abstain_reason": None,
            }
        )
    )
    context, evidence = inputs()

    execution = await LlmDiagnoser(provider).diagnose(context, evidence)

    assert execution.decision.status is DiagnosisStatus.ABSTAINED
    assert (
        execution.decision.abstain_reason
        is AbstainReason.UNSUPPORTED_FAILURE_TYPE
    )


@pytest.mark.parametrize(
    ("payload", "expected_execution_sha256", "expected_report_sha256"),
    [
        (
            {
                "status": "no_failure",
                "failure_type": "no_failure",
                "critical_span_ids": [],
                "causal_chain": [],
                "confidence": 0.5,
                "abstain_reason": None,
            },
            "b2de1023f22747c9553cbabf34003754463640950efa920401f762ec91212d51",
            "01638e8eeda545f6500de3b860855d73a8c53a32f3dc7511ab2ac6e0301be846",
        ),
        (
            {
                "status": "diagnosed",
                "failure_type": "invalid_argument",
                "critical_span_ids": ["span-005"],
                "causal_chain": [{
                    "stage": "cause",
                    "statement": "The submitted amount was rejected.",
                    "evidence_selectors": [
                        "span-005::attributes.tool.error.message"
                    ],
                }],
                "confidence": 0.9,
                "abstain_reason": None,
            },
            "a09819f1365bc9b472dea79ee559ff9a32744282477e6f55ecaea3906a6b69e4",
            "455bc592e736f6dc40e23c510fc471cc3f511443ca01fba04d7134fbea4ce6a1",
        ),
        (
            {
                "status": "diagnosed",
                "failure_type": "missing_precondition",
                "critical_span_ids": ["span-005"],
                "causal_chain": [{
                    "stage": "cause",
                    "statement": "A required lookup was skipped.",
                    "evidence_selectors": ["span-005::name"],
                }],
                "confidence": 0.9,
                "abstain_reason": None,
            },
            "247cb6875b442e45e7979437802b63ad792f37a0aa87edee5372ec3ba2c8f0ad",
            "d0e02925213bfac9b8754c66a987bdf07b0c4e69371ed220d716a71d9282196c",
        ),
    ],
)
@pytest.mark.asyncio
async def test_diagnosis_v1_behavior_is_hash_stable(
    payload: dict[str, object],
    expected_execution_sha256: str,
    expected_report_sha256: str,
) -> None:
    provider = RecordingProvider(json.dumps(payload))
    context, evidence = inputs()

    execution = await LlmDiagnoser(provider).diagnose(context, evidence)

    assert canonical_sha256(list(provider.messages)) == (
        "289a2b06d848a1f8776af0c0e10d591f756bed65eb0dc7f3fc771b19a39181a6"
    )
    assert canonical_sha256(execution) == expected_execution_sha256
    report = DiagnosisReport.from_execution(
        trace_id=context.trace_id,
        run_id=context.run_id,
        diagnoser="deepseek",
        execution=execution,
    )
    assert canonical_sha256(report) == expected_report_sha256
