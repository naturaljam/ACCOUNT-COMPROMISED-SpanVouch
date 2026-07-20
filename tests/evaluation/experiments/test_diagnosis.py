from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from spanvouch.contracts.diagnosis import ProviderUsage
from spanvouch.contracts.versioning import canonical_json, canonical_sha256
from spanvouch.diagnosis.protocols import (
    ChatMessage,
    GenerationConfig,
    ProviderResponse,
)
from spanvouch.evaluation.corpus import (
    CorpusEntry,
    CorpusManifestMetadata,
    TraceReplayRepository,
)
from spanvouch.evaluation.experiments.diagnosis import (
    DiagnosisCandidateRepository,
    DiagnosisExperimentFailure,
    FrozenDiagnosisCandidate,
    generate_and_freeze_diagnosis,
    reconstruct_shared_verifier_messages,
)
from tests.evaluation.corpus.conftest import make_record


class OfflineProvider:
    def __init__(self, confidence: float = 0.5) -> None:
        self.calls = 0
        self.confidence = confidence

    async def complete(
        self, messages: tuple[ChatMessage, ...], config: GenerationConfig
    ) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(
            content=json.dumps(
                {
                    "status": "no_failure",
                    "failure_type": "no_failure",
                    "critical_span_ids": [],
                    "causal_chain": [],
                    "confidence": self.confidence,
                    "abstain_reason": None,
                },
                indent=2,
            ),
            model=config.model,
            response_id="raw-provider-request-id",
            finish_reason="stop",
            usage=ProviderUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                latency_ms=1.0,
                request_id="raw-provider-request-id",
            ),
        )


def _corpus(tmp_path: Path) -> tuple[TraceReplayRepository, CorpusEntry]:
    record = make_record()
    metadata = CorpusManifestMetadata(
        corpus_id="supportlab-pilot-task10",
        mode="pilot",
        experiment_config_sha256="1" * 64,
        git_commit="b" * 40,
        dependency_lock_sha256="c" * 64,
        dataset_manifest_sha256="d" * 64,
        dirty_worktree=False,
        expected_cell_count=1,
        expected_pair_count=0,
        created_at_utc=datetime(2026, 7, 20, tzinfo=UTC),
        parity_results_sha256=canonical_sha256([]),
    )
    corpus = TraceReplayRepository.freeze(
        records=(record,),
        parity_results=(),
        destination=tmp_path / "corpus",
        manifest_metadata=metadata,
    )
    return corpus, CorpusEntry.from_record(record)


@pytest.mark.asyncio
async def test_generation_freezes_hash_bound_sanitized_candidate(tmp_path: Path) -> None:
    corpus, entry = _corpus(tmp_path)
    provider = OfflineProvider()
    repository = DiagnosisCandidateRepository(tmp_path / "candidates")

    candidate = await generate_and_freeze_diagnosis(
        corpus=corpus,
        cell=entry.cell,
        expected_corpus_manifest_sha256=corpus.manifest_sha256,
        expected_record_sha256=entry.record_sha256,
        expected_trace_sha256=entry.trace_sha256,
        provider=provider,
        generation=GenerationConfig(
            model="deepseek-v4-flash", max_tokens=777, temperature=0.2
        ),
        repository=repository,
        verifier_instruction="Critique evidence sufficiency only.",
    )

    assert provider.calls == 1
    assert repository.load(entry.cell) == candidate
    assert candidate.record_sha256 == entry.record_sha256
    assert candidate.trace_sha256 == entry.trace_sha256
    assert canonical_sha256(candidate.diagnostic_context) == (
        candidate.diagnostic_context_sha256
    )
    assert canonical_sha256(list(candidate.evidence_catalog)) == (
        candidate.evidence_catalog_sha256
    )
    assert canonical_sha256(candidate.report) == candidate.report_sha256
    assert canonical_sha256(candidate.generation) == candidate.generation_sha256
    assert candidate.prompt_sha256 == candidate.report.provenance.prompt_sha256
    assert candidate.generator_provider == "deepseek"
    assert candidate.generator_model == "deepseek-v4-flash"
    assert candidate.usage.request_id is None
    assert candidate.request_id_sha256 == sha256(
        b"raw-provider-request-id"
    ).hexdigest()
    serialized = canonical_json(candidate)
    for forbidden in (
        "raw-provider-request-id",
        "Diagnose this JSON trace projection",
        "api_key",
        "expected_findings",
        '"labels"',
        '"split"',
    ):
        assert forbidden not in serialized
    assert FrozenDiagnosisCandidate.model_validate_json(serialized) == candidate

    messages = reconstruct_shared_verifier_messages(
        candidate, "Critique evidence sufficiency only."
    )
    assert canonical_sha256(list(messages)) == candidate.shared_verifier_messages_sha256
    assert messages[-2].role == "assistant"
    assert messages[-1].content == "Critique evidence sufficiency only."


@pytest.mark.asyncio
async def test_repository_rejects_second_candidate_for_same_cell(tmp_path: Path) -> None:
    corpus, entry = _corpus(tmp_path)
    repository = DiagnosisCandidateRepository(tmp_path / "candidates")
    common = {
        "corpus": corpus,
        "cell": entry.cell,
        "expected_corpus_manifest_sha256": corpus.manifest_sha256,
        "expected_record_sha256": entry.record_sha256,
        "expected_trace_sha256": entry.trace_sha256,
        "generation": GenerationConfig(),
        "repository": repository,
        "verifier_instruction": "Critique evidence sufficiency only.",
    }
    first = await generate_and_freeze_diagnosis(provider=OfflineProvider(0.5), **common)
    before = repository.load(entry.cell)

    with pytest.raises(FileExistsError, match="candidate already exists for corpus cell"):
        await generate_and_freeze_diagnosis(provider=OfflineProvider(0.6), **common)

    assert repository.load(entry.cell) == before == first


@pytest.mark.asyncio
async def test_generation_rejects_hash_mismatch_before_provider_call(tmp_path: Path) -> None:
    corpus, entry = _corpus(tmp_path)
    provider = OfflineProvider()

    with pytest.raises(DiagnosisExperimentFailure, match="trace hash mismatch") as caught:
        await generate_and_freeze_diagnosis(
            corpus=corpus,
            cell=entry.cell,
            expected_corpus_manifest_sha256=corpus.manifest_sha256,
            expected_record_sha256=entry.record_sha256,
            expected_trace_sha256="f" * 64,
            provider=provider,
            generation=GenerationConfig(),
            repository=DiagnosisCandidateRepository(tmp_path / "candidates"),
            verifier_instruction="Critique evidence sufficiency only.",
        )

    assert caught.value.code == "input_integrity_failure"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_provider_failure_is_typed_and_does_not_publish(tmp_path: Path) -> None:
    corpus, entry = _corpus(tmp_path)

    class FailingProvider(OfflineProvider):
        async def complete(
            self, messages: tuple[ChatMessage, ...], config: GenerationConfig
        ) -> ProviderResponse:
            self.calls += 1
            raise RuntimeError("offline failure with secret-like details")

    provider = FailingProvider()
    repository = DiagnosisCandidateRepository(tmp_path / "candidates")
    with pytest.raises(DiagnosisExperimentFailure, match="provider call failed") as caught:
        await generate_and_freeze_diagnosis(
            corpus=corpus,
            cell=entry.cell,
            expected_corpus_manifest_sha256=corpus.manifest_sha256,
            expected_record_sha256=entry.record_sha256,
            expected_trace_sha256=entry.trace_sha256,
            provider=provider,
            generation=GenerationConfig(),
            repository=repository,
            verifier_instruction="Critique evidence sufficiency only.",
        )
    assert caught.value.code == "provider_failure"
    assert not repository.exists(entry.cell)
