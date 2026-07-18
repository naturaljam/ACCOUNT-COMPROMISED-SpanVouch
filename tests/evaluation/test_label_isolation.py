import json
from pathlib import Path

import pytest

from spanvouch.contracts.diagnosis import ProviderUsage
from spanvouch.contracts.trace import TraceIR
from spanvouch.diagnosis.protocols import ChatMessage, GenerationConfig, ProviderResponse
from spanvouch.evaluation.generate_review_dataset import ReviewCandidate
from spanvouch.evaluation.review_labels import load_review_candidates, load_review_labels
from spanvouch.evaluation.review_metrics import evaluate_review_candidates
from spanvouch.labs.supportlab.invariants import supportlab_rules
from spanvouch.verification.deterministic import DeterministicVerifier
from spanvouch.verification.invariant_engine import InvariantEngine
from spanvouch.verification.semantic import SemanticVerifier


def test_mutation_and_expected_sentinels_never_enter_provider_view() -> None:
    from spanvouch.evaluation.provider_view import build_verifier_provider_view

    candidate = load_review_candidates(
        Path("evals/datasets/supportlab-review-v1/review-candidates-v1.jsonl")
    )[0]
    candidate = ReviewCandidate.model_construct(
        candidate_id=candidate.candidate_id,
        source_run_id=candidate.source_run_id,
        mutation_kind="GOLD_SENTINEL_MUTATION",
        report=candidate.report,
    )
    evaluator_only_expected_finding = "GOLD_SENTINEL_FINDING"

    serialized = build_verifier_provider_view(candidate).model_dump_json()

    assert "GOLD_SENTINEL_MUTATION" not in serialized
    assert evaluator_only_expected_finding not in serialized
    assert "gold" not in serialized.lower()
    assert "split" not in serialized.lower()


async def test_captured_provider_messages_exclude_evaluator_only_sentinels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = Path("evals/datasets/supportlab-review-v1")
    source = Path("evals/datasets/supportlab-v1")
    candidate = load_review_candidates(dataset / "review-candidates-v1.jsonl")[0]
    label = next(
        item
        for item in load_review_labels(dataset / "review-labels-v1.jsonl")
        if item.candidate_id == candidate.candidate_id
    )
    trace = next(
        TraceIR.model_validate_json(line)
        for line in (source / "traces.jsonl").read_text(encoding="utf-8").splitlines()
        if line and TraceIR.model_validate_json(line).run_id == candidate.source_run_id
    )

    class CaptureProvider:
        def __init__(self) -> None:
            self.calls: list[tuple[ChatMessage, ...]] = []

        async def complete(
            self, messages: tuple[ChatMessage, ...], config: GenerationConfig
        ) -> ProviderResponse:
            self.calls.append(messages)
            return ProviderResponse(
                content=json.dumps(
                    {
                        "verdict": "verified",
                        "findings": [],
                        "evidence_gaps": [],
                        "alternative_failure_type": None,
                        "confidence": 0.8,
                    }
                ),
                model=config.model,
                response_id="fixed-response",
                finish_reason="stop",
                usage=ProviderUsage(
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                    latency_ms=1.0,
                    request_id="fixed-request",
                ),
            )

    captured = CaptureProvider()
    monkeypatch.setattr(
        "spanvouch.evaluation.review_metrics.validate_review_cohort", lambda *_: None
    )
    monkeypatch.setattr(
        "spanvouch.evaluation.review_metrics.EXPECTED_UNSUPPORTED_SOURCE_RUN_IDS", frozenset()
    )
    await evaluate_review_candidates(
        candidates=(candidate,),
        labels=(label,),
        traces=(trace,),
        verifier=DeterministicVerifier(
            InvariantEngine(supportlab_rules()), policy_version="supportlab-review-policy-v1"
        ),
        policy_version="supportlab-review-policy-v1",
        semantic_verifier=SemanticVerifier(
            captured, provider_id="test-provider", model="test-model"
        ),
        semantic_candidate_ids=(candidate.candidate_id,),
    )

    assert len(captured.calls) == 1
    serialized = "\n".join(message.content for message in captured.calls[0])
    for sentinel in ("GOLD_SENTINEL_MUTATION", "GOLD_SENTINEL_FINDING", "split"):
        assert sentinel not in serialized
