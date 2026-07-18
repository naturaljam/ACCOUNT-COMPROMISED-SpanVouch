import json
from pathlib import Path

import pytest

import spanvouch.evals.run_review_eval as review_eval_module
from spanvouch.diagnosis.errors import ProviderRequestError
from spanvouch.diagnosis.models import ProviderUsage
from spanvouch.diagnosis.protocols import ChatMessage, GenerationConfig, ProviderResponse
from spanvouch.evals.review_labels import load_review_candidates
from spanvouch.evals.review_metrics import ReviewEvaluationReport
from spanvouch.evals.run_review_eval import _run, main, write_report
from spanvouch.review.models import (
    VerificationInput,
    VerifierKind,
    VerifierReport,
    VerifierVerdict,
    canonical_json,
)
from spanvouch.review.semantic_verifier import SemanticVerifier


def test_default_offline_cli_writes_byte_exact_canonical_one_line_json(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    assert main(["--output", str(first)]) == 0
    assert main(["--output", str(second)]) == 0

    content = first.read_bytes()
    assert content == second.read_bytes()
    assert content.endswith(b"\n")
    assert b"\r" not in content
    assert content.count(b"\n") == 1
    parsed = ReviewEvaluationReport.model_validate_json(content)
    assert content == (canonical_json(parsed) + "\n").encode("utf-8")


def test_default_cli_detects_all_injected_defects_without_external_usage(
    tmp_path: Path,
) -> None:
    output = tmp_path / "report.json"

    assert main(["--output", str(output)]) == 0

    report = ReviewEvaluationReport.model_validate_json(output.read_text(encoding="utf-8"))
    assert report.candidate_count == 36
    assert report.metrics.hard_defect_recall == 1.0
    assert report.metrics.unsupported_scope_detection_rate == 1.0
    assert report.metrics.operational_error_rate == 0.0
    assert report.usage.provider_sample_count == 0
    assert report.usage.total_tokens == 0


def test_write_report_excludes_gold_label_fields(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    assert main(["--output", str(output)]) == 0
    report = ReviewEvaluationReport.model_validate_json(output.read_text(encoding="utf-8"))

    rewritten = tmp_path / "rewritten.json"
    write_report(report, rewritten)
    document = json.loads(rewritten.read_text(encoding="utf-8"))

    assert rewritten.read_bytes() == output.read_bytes()
    assert all("expected_verdict" not in sample for sample in document["samples"])
    assert all("expected_finding_codes" not in sample for sample in document["samples"])


def test_hybrid_cli_requires_live_opt_in_before_credentials_or_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    touched = False

    def forbidden_config() -> object:
        nonlocal touched
        touched = True
        raise AssertionError("credentials must not be read")

    monkeypatch.setattr("spanvouch.evals.run_review_eval.DeepSeekConfig.from_env", forbidden_config)

    with pytest.raises(SystemExit) as raised:
        main(
            [
                "--output",
                str(tmp_path / "report.json"),
                "--verifier",
                "hybrid",
            ]
        )

    assert raised.value.code == 2
    assert touched is False


def test_default_deterministic_cli_does_not_construct_live_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_provider(config: object) -> object:
        del config
        raise AssertionError("default evaluation must stay offline")

    monkeypatch.setattr("spanvouch.evals.run_review_eval.DeepSeekProvider", forbidden_provider)

    assert main(["--output", str(tmp_path / "report.json")]) == 0


def test_default_deterministic_cli_fails_a_stable_degraded_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = review_eval_module.EvidenceVerifier

    class DegradedVerifier:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._delegate = original(*args, **kwargs)
            self.version_fingerprint = self._delegate.version_fingerprint

        async def verify(self, input_: VerificationInput) -> VerifierReport:
            report = await self._delegate.verify(input_)
            if input_.report.run_id == "clean-01":
                return report.model_copy(update={"verdict": VerifierVerdict.REVIEW_REQUIRED})
            return report

    monkeypatch.setattr(review_eval_module, "EvidenceVerifier", DegradedVerifier)
    output = tmp_path / "degraded.json"

    assert main(["--output", str(output)]) == 1
    report = ReviewEvaluationReport.model_validate_json(output.read_text(encoding="utf-8"))
    assert report.status == "failed"
    assert report.metrics.valid_report_pass_rate < 1.0
    assert report.metrics.operational_error_rate == 0.0


def test_hybrid_cli_runs_only_allowlisted_candidates_and_reports_semantic_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeProvider:
        def __init__(self, config: object) -> None:
            self.config = config
            self.models: list[str] = []

        async def complete(
            self,
            messages: tuple[ChatMessage, ...],
            config: GenerationConfig,
        ) -> ProviderResponse:
            assert messages
            self.models.append(config.model)
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
                response_id=f"request-{len(self.models)}",
                finish_reason="stop",
                usage=ProviderUsage(
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                    latency_ms=float(10 * len(self.models)),
                    request_id=f"request-{len(self.models)}",
                ),
            )

    provider: FakeProvider | None = None

    def make_provider(config: object) -> FakeProvider:
        nonlocal provider
        provider = FakeProvider(config)
        return provider

    monkeypatch.setattr("spanvouch.evals.run_review_eval.DeepSeekConfig.from_env", lambda: object())
    monkeypatch.setattr("spanvouch.evals.run_review_eval.DeepSeekProvider", make_provider)
    output = tmp_path / "hybrid.json"

    result = main(
        [
            "--output",
            str(output),
            "--verifier",
            "hybrid",
            "--allow-live-api",
            "--candidate-id",
            "clean-01--unmodified",
            "--candidate-id",
            "clean-02--unmodified",
            "--model",
            "deepseek-test-model",
        ]
    )

    assert result == 0
    assert provider is not None
    assert provider.models == ["deepseek-test-model", "deepseek-test-model"]
    report = ReviewEvaluationReport.model_validate_json(output.read_text(encoding="utf-8"))
    semantic_samples = tuple(
        sample for sample in report.samples if sample.semantic_verifier_report is not None
    )
    assert tuple(sample.candidate_id for sample in semantic_samples) == (
        "clean-01--unmodified",
        "clean-02--unmodified",
    )
    assert report.metrics.semantic_verdict_distribution == {"verified": 2}
    assert report.metrics.verifier_disagreement_rate == 0.0
    assert report.metrics.semantic_structured_output_success_rate == 1.0
    assert report.metrics.semantic_operational_error_rate == 0.0
    assert report.usage.provider_sample_count == 2
    assert report.usage.input_tokens == 20
    assert report.usage.output_tokens == 10
    assert report.usage.total_tokens == 30
    assert report.usage.latency_p50_ms == 15.0
    assert report.usage.latency_p95_ms == 19.5


async def test_semantic_invalid_output_is_not_counted_as_structured_success() -> None:
    class InvalidOutputProvider:
        async def complete(
            self,
            messages: tuple[ChatMessage, ...],
            config: GenerationConfig,
        ) -> ProviderResponse:
            del messages
            return ProviderResponse(
                content="{}",
                model=config.model,
                response_id="invalid-request",
                finish_reason="stop",
                usage=ProviderUsage(
                    input_tokens=4,
                    output_tokens=1,
                    total_tokens=5,
                    latency_ms=12.0,
                    request_id="invalid-request",
                ),
            )

    report = await _run(
        Path("evals/datasets/supportlab-review-v1"),
        Path("evals/datasets/supportlab-v1"),
        semantic_verifier=SemanticVerifier(InvalidOutputProvider()),
        semantic_candidate_ids=("clean-01--unmodified",),
    )

    assert report.metrics.semantic_structured_output_success_rate == 0.0
    assert report.metrics.semantic_operational_error_rate == 0.0


async def test_semantic_provider_error_is_recorded_as_operational() -> None:
    class FailedSemanticVerifier:
        kind = VerifierKind.SEMANTIC
        version_fingerprint = "failed-semantic-v1"

        async def verify(self, input_: VerificationInput) -> VerifierReport:
            del input_
            raise ProviderRequestError("transport_error", retryable=True)

    report = await _run(
        Path("evals/datasets/supportlab-review-v1"),
        Path("evals/datasets/supportlab-v1"),
        semantic_verifier=FailedSemanticVerifier(),
        semantic_candidate_ids=("clean-01--unmodified",),
    )

    assert report.status == "partial"
    assert report.metrics.semantic_operational_error_rate == 1.0
    sample = next(
        sample for sample in report.samples if sample.candidate_id == "clean-01--unmodified"
    )
    assert sample.semantic_operational_error == "ProviderRequestError"


async def test_semantic_programming_error_is_not_misreported_as_operational() -> None:
    class BrokenSemanticVerifier:
        kind = VerifierKind.SEMANTIC
        version_fingerprint = "broken-semantic-v1"

        async def verify(self, input_: VerificationInput) -> VerifierReport:
            del input_
            raise ValueError("programming contract bug")

    with pytest.raises(ValueError, match="programming contract bug"):
        await _run(
            Path("evals/datasets/supportlab-review-v1"),
            Path("evals/datasets/supportlab-v1"),
            semantic_verifier=BrokenSemanticVerifier(),
            semantic_candidate_ids=("clean-01--unmodified",),
        )


async def test_full_cohort_semantic_prompts_exclude_hidden_mutation_metadata() -> None:
    class CaptureProvider:
        def __init__(self) -> None:
            self.calls: list[tuple[ChatMessage, ...]] = []

        async def complete(
            self,
            messages: tuple[ChatMessage, ...],
            config: GenerationConfig,
        ) -> ProviderResponse:
            self.calls.append(messages)
            request_id = f"prompt-capture-{len(self.calls)}"
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
                response_id=request_id,
                finish_reason="stop",
                usage=ProviderUsage(
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                    latency_ms=1.0,
                    request_id=request_id,
                ),
            )

    dataset = Path("evals/datasets/supportlab-review-v1")
    source = Path("evals/datasets/supportlab-v1")
    candidates = load_review_candidates(dataset / "review-candidates-v1.jsonl")
    provider = CaptureProvider()

    await _run(
        dataset,
        source,
        semantic_verifier=SemanticVerifier(provider),
        semantic_candidate_ids=tuple(candidate.candidate_id for candidate in candidates),
    )

    assert len(provider.calls) == 34
    forbidden = (
        "mutation_kind",
        "candidate_id",
        "source_run_id",
        "expected_verdict",
        "expected_finding_codes",
        "unsupported_scope",
        "forcibly classifies",
        "unsupported trace",
    )
    for messages in provider.calls:
        assert len(messages) == 2
        payload = json.loads(
            messages[1].content.removeprefix("Verify this canonical JSON data:\n")
        )
        assert set(payload) == {"diagnosis", "evidence_selectors", "spans"}
        assert set(payload["diagnosis"]) == {
            "status",
            "failure_type",
            "critical_span_ids",
            "causal_chain",
            "confidence",
            "abstain_reason",
        }
        visible = "\n".join(message.content for message in messages).lower()
        assert all(term not in visible for term in forbidden)
