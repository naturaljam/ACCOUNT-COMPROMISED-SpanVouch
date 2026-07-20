import pytest
from pydantic import ValidationError

from spanvouch.contracts.verification import VerificationInput
from spanvouch.contracts.versioning import canonical_json, canonical_sha256
from spanvouch.diagnosis.protocols import ChatMessage, GenerationConfig
from spanvouch.trace.evidence_catalog import EvidenceCatalog
from spanvouch.verification.prompting import PreparedVerification, SemanticPromptBuilder
from tests.review.factories import make_diagnosis_report, make_review_snapshot


def _input() -> VerificationInput:
    report = make_diagnosis_report()
    return VerificationInput(
        snapshot=make_review_snapshot(),
        report=report,
        report_sha256=canonical_sha256(report),
    )


def test_isolated_prompt_preserves_phase4_message_hash() -> None:
    input_ = _input()
    catalog = EvidenceCatalog.from_view(input_.snapshot.trace_view())
    prepared = SemanticPromptBuilder().isolated(
        input_, catalog, GenerationConfig(model="fixture-semantic-model")
    )

    assert prepared.prompt_sha256 == (
        "709ff88a3709f11be47017534d03bc20f57509586d0a048e0d8e9d8f99f293dc"
    )
    assert prepared.prompt_version == "semantic-verifier-v1"
    assert len(prepared.messages) == 2


def test_shared_differs_only_by_prepended_diagnosis_history() -> None:
    input_ = _input()
    catalog = EvidenceCatalog.from_view(input_.snapshot.trace_view())
    generation = GenerationConfig(model="deepseek-chat", max_tokens=2048)
    history = (
        ChatMessage(role="system", content="Diagnosis system."),
        ChatMessage(role="user", content="Diagnosis input."),
        ChatMessage(role="assistant", content=canonical_json(input_.report)),
    )
    builder = SemanticPromptBuilder(prompt_version="phase5-shared-verifier-v1")
    shared = builder.shared(history, input_, catalog, generation)
    isolated = builder.isolated(input_, catalog, generation)

    assert shared.messages[:3] == history
    assert shared.messages[3:] == isolated.messages
    assert shared.generation == isolated.generation
    assert shared.messages[-2].content == isolated.messages[0].content
    assert shared.messages[-1].content == isolated.messages[1].content


def test_shared_rejects_history_without_canonical_frozen_diagnosis() -> None:
    input_ = _input()
    catalog = EvidenceCatalog.from_view(input_.snapshot.trace_view())
    bad_history = (
        ChatMessage(role="system", content="Diagnosis system."),
        ChatMessage(role="user", content="Diagnosis input."),
        ChatMessage(role="assistant", content="{}"),
    )
    with pytest.raises(ValueError, match="frozen diagnosis"):
        SemanticPromptBuilder().shared(
            bad_history, input_, catalog, GenerationConfig(model="deepseek-chat")
        )


def test_prepared_verification_is_frozen_hash_bound_and_extra_forbidden() -> None:
    input_ = _input()
    catalog = EvidenceCatalog.from_view(input_.snapshot.trace_view())
    prepared = SemanticPromptBuilder().isolated(input_, catalog, GenerationConfig())
    payload = prepared.model_dump(mode="python")

    with pytest.raises(ValidationError):
        PreparedVerification.model_validate({**payload, "unexpected": True})
    with pytest.raises(ValidationError, match="prompt_sha256"):
        PreparedVerification.model_validate({**payload, "prompt_sha256": "0" * 64})
