from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from spanvouch.contracts.diagnosis import ProviderUsage
from spanvouch.contracts.versioning import canonical_bytes, canonical_json, canonical_sha256
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
        generation=GenerationConfig(model="deepseek-v4-flash", max_tokens=777, temperature=0.2),
        repository=repository,
        verifier_instruction="Critique evidence sufficiency only.",
    )

    assert provider.calls == 1
    candidate_sha256 = canonical_sha256(candidate)
    assert (
        repository.load(
            entry.cell,
            expected_candidate_sha256=candidate_sha256,
            expected_corpus_manifest_sha256=corpus.manifest_sha256,
        )
        == candidate
    )
    assert candidate.record_sha256 == entry.record_sha256
    assert candidate.trace_sha256 == entry.trace_sha256
    assert canonical_sha256(candidate.diagnostic_context) == (candidate.diagnostic_context_sha256)
    assert canonical_sha256(list(candidate.evidence_catalog)) == (candidate.evidence_catalog_sha256)
    assert canonical_sha256(candidate.report) == candidate.report_sha256
    assert canonical_sha256(candidate.generation) == candidate.generation_sha256
    assert candidate.prompt_sha256 == candidate.report.provenance.prompt_sha256
    assert candidate.generator_provider == "deepseek"
    assert candidate.generator_model == "deepseek-v4-flash"
    assert candidate.usage.request_id is None
    assert candidate.request_id_sha256 == sha256(b"raw-provider-request-id").hexdigest()
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
async def test_repository_verifies_existing_candidates_against_expected_inputs(
    tmp_path: Path,
) -> None:
    corpus, entry = _corpus(tmp_path)
    repository = DiagnosisCandidateRepository(tmp_path / "candidates")
    generation = GenerationConfig(model="deepseek-v4-flash", max_tokens=777, temperature=0.2)
    candidate = await generate_and_freeze_diagnosis(
        corpus=corpus,
        cell=entry.cell,
        expected_corpus_manifest_sha256=corpus.manifest_sha256,
        expected_record_sha256=entry.record_sha256,
        expected_trace_sha256=entry.trace_sha256,
        provider=OfflineProvider(),
        generation=generation,
        repository=repository,
        verifier_instruction="Critique evidence sufficiency only.",
    )

    existing = repository.verify_existing(
        entries=(entry,),
        expected_corpus_manifest_sha256=corpus.manifest_sha256,
        expected_generation=generation,
        expected_provider="deepseek",
        expected_model="deepseek-v4-flash",
    )

    assert existing == {entry.cell: candidate}


@pytest.mark.asyncio
async def test_repository_existing_verification_rejects_drift_and_extra_cells(
    tmp_path: Path,
) -> None:
    corpus, entry = _corpus(tmp_path)
    repository = DiagnosisCandidateRepository(tmp_path / "candidates")
    generation = GenerationConfig()
    await generate_and_freeze_diagnosis(
        corpus=corpus,
        cell=entry.cell,
        expected_corpus_manifest_sha256=corpus.manifest_sha256,
        expected_record_sha256=entry.record_sha256,
        expected_trace_sha256=entry.trace_sha256,
        provider=OfflineProvider(),
        generation=generation,
        repository=repository,
        verifier_instruction="Critique evidence sufficiency only.",
    )

    with pytest.raises(ValueError, match="generation"):
        repository.verify_existing(
            entries=(entry,),
            expected_corpus_manifest_sha256=corpus.manifest_sha256,
            expected_generation=generation.model_copy(update={"max_tokens": 1}),
            expected_provider="deepseek",
            expected_model="deepseek-v4-flash",
        )

    (tmp_path / "candidates/cells/unknown-cell").mkdir()
    with pytest.raises(ValueError, match="layout|unknown"):
        repository.verify_existing(
            entries=(entry,),
            expected_corpus_manifest_sha256=corpus.manifest_sha256,
            expected_generation=generation,
            expected_provider="deepseek",
            expected_model="deepseek-v4-flash",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("renamed_payload", "content address"),
        ("changed_bytes", "SHA-256"),
        ("corpus_manifest_sha256", "corpus manifest"),
        ("record_sha256", "record"),
        ("trace_sha256", "trace"),
        ("raw_request_id", "raw request ID"),
        ("unknown_cell", "cell"),
    ],
)
async def test_repository_existing_verification_rejects_candidate_tampering(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
) -> None:
    corpus, entry = _corpus(tmp_path)
    repository = DiagnosisCandidateRepository(tmp_path / "candidates")
    generation = GenerationConfig()
    candidate = await generate_and_freeze_diagnosis(
        corpus=corpus,
        cell=entry.cell,
        expected_corpus_manifest_sha256=corpus.manifest_sha256,
        expected_record_sha256=entry.record_sha256,
        expected_trace_sha256=entry.trace_sha256,
        provider=OfflineProvider(),
        generation=generation,
        repository=repository,
        verifier_instruction="Critique evidence sufficiency only.",
    )
    payload = next((tmp_path / "candidates/cells").glob("*/*.json"))

    if mutation == "renamed_payload":
        payload.rename(payload.with_name(f"{'f' * 64}.json"))
    elif mutation == "changed_bytes":
        payload.write_bytes(payload.read_bytes() + b" ")
    else:
        value = candidate.model_dump(mode="json")
        if mutation == "raw_request_id":
            value["usage"]["request_id"] = "raw-provider-request-id"
            value["report"]["usage"]["request_id"] = "raw-provider-request-id"
            value["report_sha256"] = canonical_sha256(value["report"])
        elif mutation == "unknown_cell":
            value["cell"]["seed"] += 1
        else:
            value[mutation] = "f" * 64
        tampered = canonical_bytes(value)
        payload.unlink()
        payload = payload.with_name(f"{sha256(tampered).hexdigest()}.json")
        payload.write_bytes(tampered)

    with pytest.raises(ValueError, match=expected_error):
        repository.verify_existing(
            entries=(entry,),
            expected_corpus_manifest_sha256=corpus.manifest_sha256,
            expected_generation=generation,
            expected_provider="deepseek",
            expected_model="deepseek-v4-flash",
        )


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
    digest = canonical_sha256(first)
    before = repository.load(
        entry.cell,
        expected_candidate_sha256=digest,
        expected_corpus_manifest_sha256=corpus.manifest_sha256,
    )

    with pytest.raises(FileExistsError, match="candidate already exists for corpus cell"):
        await generate_and_freeze_diagnosis(provider=OfflineProvider(0.6), **common)

    assert (
        repository.load(
            entry.cell,
            expected_candidate_sha256=digest,
            expected_corpus_manifest_sha256=corpus.manifest_sha256,
        )
        == before
        == first
    )


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


@pytest.mark.asyncio
async def test_load_rejects_whole_valid_candidate_directory_replacement(
    tmp_path: Path,
) -> None:
    corpus, entry = _corpus(tmp_path)
    first_repo = DiagnosisCandidateRepository(tmp_path / "first-candidates")
    first = await generate_and_freeze_diagnosis(
        corpus=corpus,
        cell=entry.cell,
        expected_corpus_manifest_sha256=corpus.manifest_sha256,
        expected_record_sha256=entry.record_sha256,
        expected_trace_sha256=entry.trace_sha256,
        provider=OfflineProvider(0.5),
        generation=GenerationConfig(),
        repository=first_repo,
        verifier_instruction="Critique evidence sufficiency only.",
    )
    second_repo = DiagnosisCandidateRepository(tmp_path / "second-candidates")
    second = await generate_and_freeze_diagnosis(
        corpus=corpus,
        cell=entry.cell,
        expected_corpus_manifest_sha256=corpus.manifest_sha256,
        expected_record_sha256=entry.record_sha256,
        expected_trace_sha256=entry.trace_sha256,
        provider=OfflineProvider(0.6),
        generation=GenerationConfig(),
        repository=second_repo,
        verifier_instruction="Critique evidence sufficiency only.",
    )
    first_cell_dir = next((tmp_path / "first-candidates/cells").iterdir())
    second_cell_dir = next((tmp_path / "second-candidates/cells").iterdir())
    shutil.rmtree(first_cell_dir)
    shutil.copytree(second_cell_dir, first_cell_dir)

    with pytest.raises(ValueError, match="trusted candidate SHA-256 mismatch"):
        first_repo.load(
            entry.cell,
            expected_candidate_sha256=canonical_sha256(first),
            expected_corpus_manifest_sha256=corpus.manifest_sha256,
        )
    with pytest.raises(ValueError, match="trusted corpus manifest SHA-256 mismatch"):
        first_repo.load(
            entry.cell,
            expected_candidate_sha256=canonical_sha256(second),
            expected_corpus_manifest_sha256="f" * 64,
        )
    assert canonical_sha256(first) != canonical_sha256(second)


@pytest.mark.asyncio
async def test_model_derived_credential_text_is_rejected_before_freeze(
    tmp_path: Path,
) -> None:
    corpus, entry = _corpus(tmp_path)

    class CredentialProvider(OfflineProvider):
        async def complete(
            self, messages: tuple[ChatMessage, ...], config: GenerationConfig
        ) -> ProviderResponse:
            self.calls += 1
            return ProviderResponse(
                content=json.dumps(
                    {
                        "status": "diagnosed",
                        "failure_type": "invalid_argument",
                        "critical_span_ids": ["span-root"],
                        "causal_chain": [
                            {
                                "stage": "cause",
                                "statement": "Authorization: Bearer stolen-provider-secret",
                                "evidence_selectors": ["span-root::name"],
                            }
                        ],
                        "confidence": 0.9,
                        "abstain_reason": None,
                    }
                ),
                model=config.model,
                response_id="response-secret-case",
                finish_reason="stop",
                usage=ProviderUsage(
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                    latency_ms=1.0,
                    request_id="response-secret-case",
                ),
            )

    provider = CredentialProvider()
    repository = DiagnosisCandidateRepository(tmp_path / "candidates")
    with pytest.raises(DiagnosisExperimentFailure) as caught:
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
    assert caught.value.code == "unsafe_artifact_content"
    assert str(caught.value) == "model-derived diagnosis content is unsafe"
    assert "stolen-provider-secret" not in str(caught.value)
    assert provider.calls == 1
    assert not repository.exists(entry.cell)


@pytest.mark.asyncio
async def test_invalid_verifier_instruction_fails_typed_before_provider_call(
    tmp_path: Path,
) -> None:
    corpus, entry = _corpus(tmp_path)
    provider = OfflineProvider()
    repository = DiagnosisCandidateRepository(tmp_path / "candidates")

    with pytest.raises(DiagnosisExperimentFailure) as caught:
        await generate_and_freeze_diagnosis(
            corpus=corpus,
            cell=entry.cell,
            expected_corpus_manifest_sha256=corpus.manifest_sha256,
            expected_record_sha256=entry.record_sha256,
            expected_trace_sha256=entry.trace_sha256,
            provider=provider,
            generation=GenerationConfig(),
            repository=repository,
            verifier_instruction="   ",
        )

    assert caught.value.code == "contract_failure"
    assert str(caught.value) == "invalid verifier instruction"
    assert provider.calls == 0
    assert not repository.exists(entry.cell)


@pytest.mark.asyncio
async def test_frozen_candidate_rejects_every_reconstructive_binding_drift(
    tmp_path: Path,
) -> None:
    corpus, entry = _corpus(tmp_path)
    candidate = await generate_and_freeze_diagnosis(
        corpus=corpus,
        cell=entry.cell,
        expected_corpus_manifest_sha256=corpus.manifest_sha256,
        expected_record_sha256=entry.record_sha256,
        expected_trace_sha256=entry.trace_sha256,
        provider=OfflineProvider(),
        generation=GenerationConfig(),
        repository=DiagnosisCandidateRepository(tmp_path / "candidates"),
        verifier_instruction="Critique evidence sufficiency only.",
    )
    payload = candidate.model_dump(mode="python")
    report = candidate.report.model_dump(mode="python")
    provenance = candidate.report.provenance.model_dump(mode="python")
    cases = (
        {**payload, "diagnostic_context_sha256": "f" * 64},
        {**payload, "evidence_catalog": ()},
        {**payload, "evidence_catalog_sha256": "f" * 64},
        {**payload, "report_sha256": "f" * 64},
        {**payload, "generation_sha256": "f" * 64},
        {**payload, "prompt_version": "drifted-prompt"},
        {**payload, "report": {**report, "trace_id": "drifted-trace"}},
        {
            **payload,
            "report": {
                **report,
                "provenance": {**provenance, "provider": "other-provider"},
            },
        },
        {
            **payload,
            "usage": {**candidate.usage.model_dump(mode="python"), "request_id": "raw"},
        },
    )
    for changed in cases:
        with pytest.raises(ValueError):
            FrozenDiagnosisCandidate.model_validate(changed)

    altered = candidate.model_copy(update={"shared_verifier_messages_sha256": "f" * 64})
    with pytest.raises(ValueError, match="pre-call audit hash"):
        reconstruct_shared_verifier_messages(altered, "Critique evidence sufficiency only.")


@pytest.mark.asyncio
async def test_candidate_repository_rejects_invalid_hashes_layout_and_address(
    tmp_path: Path,
) -> None:
    corpus, entry = _corpus(tmp_path)
    repository = DiagnosisCandidateRepository(tmp_path / "candidates")
    candidate = await generate_and_freeze_diagnosis(
        corpus=corpus,
        cell=entry.cell,
        expected_corpus_manifest_sha256=corpus.manifest_sha256,
        expected_record_sha256=entry.record_sha256,
        expected_trace_sha256=entry.trace_sha256,
        provider=OfflineProvider(),
        generation=GenerationConfig(),
        repository=repository,
        verifier_instruction="Critique evidence sufficiency only.",
    )
    digest = canonical_sha256(candidate)
    with pytest.raises(ValueError, match="expected_candidate_sha256"):
        repository.load(
            entry.cell,
            expected_candidate_sha256="bad",
            expected_corpus_manifest_sha256=corpus.manifest_sha256,
        )
    with pytest.raises(ValueError, match="expected_corpus_manifest_sha256"):
        repository.load(
            entry.cell,
            expected_candidate_sha256=digest,
            expected_corpus_manifest_sha256="bad",
        )

    payload = next((tmp_path / "candidates/cells").rglob("*.json"))
    renamed = payload.with_name(f"{'f' * 64}.json")
    payload.rename(renamed)
    with pytest.raises(ValueError, match="content address"):
        repository.load(
            entry.cell,
            expected_candidate_sha256=digest,
            expected_corpus_manifest_sha256=corpus.manifest_sha256,
        )
    renamed.rename(payload)
    (payload.parent / "extra.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected layout"):
        repository.load(
            entry.cell,
            expected_candidate_sha256=digest,
            expected_corpus_manifest_sha256=corpus.manifest_sha256,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_identity", [False, True])
async def test_generation_rejects_provider_provenance_or_usage_identity(
    tmp_path: Path, missing_identity: bool
) -> None:
    corpus, entry = _corpus(tmp_path)

    class InvalidProvider(OfflineProvider):
        async def complete(
            self, messages: tuple[ChatMessage, ...], config: GenerationConfig
        ) -> ProviderResponse:
            response = await super().complete(messages, config)
            if missing_identity:
                return response.model_copy(
                    update={"usage": response.usage.model_copy(update={"request_id": None})}
                )
            return response.model_copy(update={"model": "wrong-model"})

    with pytest.raises(DiagnosisExperimentFailure) as caught:
        await generate_and_freeze_diagnosis(
            corpus=corpus,
            cell=entry.cell,
            expected_corpus_manifest_sha256=corpus.manifest_sha256,
            expected_record_sha256=entry.record_sha256,
            expected_trace_sha256=entry.trace_sha256,
            provider=InvalidProvider(),
            generation=GenerationConfig(),
            repository=DiagnosisCandidateRepository(tmp_path / "candidates"),
            verifier_instruction="Critique evidence sufficiency only.",
        )
    assert caught.value.code == "contract_failure"
