import json

import pytest

from spanvouch.contracts.diagnosis import DiagnosisReport
from spanvouch.contracts.versioning import canonical_json, canonical_sha256
from spanvouch.diagnosis.prompting import DiagnosisPromptBuilder
from spanvouch.diagnosis.protocols import ChatMessage, GenerationConfig
from spanvouch.trace.evidence_catalog import EvidenceCatalog
from tests.diagnosis.test_llm_diagnoser import inputs


def test_prepare_preserves_diagnosis_v1_payload_and_hash() -> None:
    context, evidence = inputs()

    prepared = DiagnosisPromptBuilder().prepare(
        context, evidence, GenerationConfig(model="deepseek-v4-flash")
    )

    assert prepared.prompt_version == "diagnosis-v1"
    assert prepared.prompt_sha256 == (
        "289a2b06d848a1f8776af0c0e10d591f756bed65eb0dc7f3fc771b19a39181a6"
    )
    assert canonical_sha256(list(prepared.messages)) == prepared.prompt_sha256
    assert prepared.generation == GenerationConfig(model="deepseek-v4-flash")


def test_shared_verifier_messages_use_only_frozen_report_and_one_instruction() -> None:
    context, evidence = inputs()
    builder = DiagnosisPromptBuilder()
    prepared = builder.prepare(context, evidence, GenerationConfig())
    report = DiagnosisReport.model_validate(
        {
            "trace_id": context.trace_id,
            "run_id": context.run_id,
            "diagnoser": "deepseek",
            "status": "no_failure",
            "failure_type": "no_failure",
            "critical_span_ids": [],
            "causal_chain": [],
            "evidence": [],
            "confidence": 0.5,
            "abstain_reason": None,
            "provenance": {
                "taxonomy": {"taxonomy_id": "supportlab", "taxonomy_version": "1.0"},
                "diagnoser_version": "evidence-llm-v1",
                "prompt_version": "diagnosis-v1",
                "prompt_sha256": prepared.prompt_sha256,
                "model": "deepseek-v4-flash",
                "provider": "deepseek",
            },
            "usage": None,
        }
    )

    messages = builder.shared_verifier_messages(prepared, report, "Critique evidence only.")

    assert messages[:2] == prepared.messages
    assert messages[2] == ChatMessage(role="assistant", content=canonical_json(report))
    assert messages[3] == ChatMessage(role="user", content="Critique evidence only.")
    assert len(messages) == 4
    assert json.loads(messages[2].content)["status"] == "no_failure"
    assert "response_id" not in messages[2].content


def test_prepare_rejects_same_selectors_with_tampered_evidence_values() -> None:
    context, evidence = inputs()
    values = {selector: "tampered" for selector in evidence.selectors}

    with pytest.raises(ValueError, match="evidence catalog does not match"):
        DiagnosisPromptBuilder().prepare(
            context, EvidenceCatalog(values), GenerationConfig()
        )
