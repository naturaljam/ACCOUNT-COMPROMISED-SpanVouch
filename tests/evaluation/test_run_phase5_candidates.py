from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from spanvouch.contracts.diagnosis import ProviderUsage
from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.diagnosis.protocols import ChatMessage, GenerationConfig, ProviderResponse
from spanvouch.evaluation.corpus import CorpusManifestMetadata, TraceReplayRepository
from spanvouch.evaluation.experiments.config import load_experiment_config
from spanvouch.evaluation.experiments.diagnosis import (
    DiagnosisCandidateRepository,
    generate_and_freeze_diagnosis,
)
from spanvouch.evaluation.experiments.provider import ProviderConfigurationError
from spanvouch.evaluation.run_phase5_candidates import (
    CandidateGenerationRequest,
    build_parser,
    candidate_generation_manifest_sha256,
    run_candidate_generation,
)
from spanvouch.evaluation.run_phase5_matrix import _load_candidates
from tests.evaluation.corpus.conftest import make_record

CONFIG = Path("evals/configs/phase5-pilot.json").resolve()
DEEPSEEK_PRICING = CONFIG.with_name("phase5-deepseek-v4-flash-pricing.json")


def _corpus(
    tmp_path: Path,
    *,
    config_sha256: str,
    record_count: int = 1,
) -> TraceReplayRepository:
    records = tuple(
        make_record(repetition=index + 1, seed=20260719 + index) for index in range(record_count)
    )
    return TraceReplayRepository.freeze(
        records=records,
        parity_results=(),
        destination=tmp_path / "corpus",
        manifest_metadata=CorpusManifestMetadata(
            corpus_id="candidate-live-pilot",
            mode="pilot",
            experiment_config_sha256=config_sha256,
            git_commit="b" * 40,
            dependency_lock_sha256="c" * 64,
            dataset_manifest_sha256="d" * 64,
            dirty_worktree=False,
            expected_cell_count=record_count,
            expected_pair_count=0,
            created_at_utc=datetime(2026, 7, 20, tzinfo=UTC),
            parity_results_sha256=canonical_sha256([]),
        ),
    )


class _OfflineCandidateProvider:
    async def complete(
        self, messages: tuple[ChatMessage, ...], config: GenerationConfig
    ) -> ProviderResponse:
        return ProviderResponse(
            content=json.dumps(
                {
                    "status": "no_failure",
                    "failure_type": "no_failure",
                    "critical_span_ids": [],
                    "causal_chain": [],
                    "confidence": 0.8,
                    "abstain_reason": None,
                }
            ),
            model=config.model,
            response_id="offline-response-id",
            finish_reason="stop",
            usage=ProviderUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                latency_ms=1.0,
                request_id="offline-response-id",
            ),
        )


async def _freeze_first_candidate(
    corpus: TraceReplayRepository,
    output: Path,
) -> None:
    config = load_experiment_config(CONFIG)
    entry = corpus.verify().entries[0]
    await generate_and_freeze_diagnosis(
        corpus=corpus,
        cell=entry.cell,
        expected_corpus_manifest_sha256=corpus.manifest_sha256,
        expected_record_sha256=entry.record_sha256,
        expected_trace_sha256=entry.trace_sha256,
        provider=_OfflineCandidateProvider(),
        generation=GenerationConfig(
            model=config.generator.model,
            max_tokens=config.generator.max_tokens,
            temperature=config.generator.temperature,
            extra_body=config.generator.extra_body,
        ),
        repository=DiagnosisCandidateRepository(output),
        verifier_instruction="Critique evidence sufficiency only.",
    )


def _pricing(path: Path) -> Path:
    path.write_bytes(DEEPSEEK_PRICING.read_bytes())
    return path


def _environment(tmp_path: Path) -> dict[str, str]:
    deepseek_key_name = "DEEPSEEK" + "_API_KEY"
    return {
        deepseek_key_name: "deepseek-candidate-test-sentinel",
        "SPANVOUCH_PHASE5_BUDGET_LEDGER_PATH": str((tmp_path / "global-budget.sqlite3").resolve()),
        "SPANVOUCH_DEEPSEEK_BASE_URL": "https://api.deepseek.com/",
        "SPANVOUCH_PHASE5_DEEPSEEK_PRICING_PATH": str(_pricing(tmp_path / "deepseek-price.json")),
    }


def _completion(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert body["thinking"] == {"type": "disabled"}
    return httpx.Response(
        200,
        request=request,
        json={
            "id": "raw-candidate-response-id",
            "model": body["model"],
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "status": "no_failure",
                                "failure_type": "no_failure",
                                "critical_span_ids": [],
                                "causal_chain": [],
                                "confidence": 0.8,
                                "abstain_reason": None,
                            }
                        )
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


def test_candidate_cli_generates_guarded_repository_consumable_by_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_experiment_config(CONFIG)
    corpus = _corpus(
        tmp_path,
        config_sha256=canonical_sha256(config.model_dump(mode="json")),
    )
    approved = candidate_generation_manifest_sha256(CONFIG, corpus.root)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _completion(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "candidates"
    request = CandidateGenerationRequest(
        config=CONFIG,
        corpus_dir=corpus.root,
        output_dir=output,
        allow_live_provider=True,
        formal_run=False,
        approved_manifest_sha256=approved,
    )
    asyncio.run(
        run_candidate_generation(request, environ=_environment(tmp_path), deepseek_client=client)
    )

    candidates = _load_candidates(output, corpus.verify().entries, corpus.manifest_sha256)
    assert len(candidates) == calls == 1
    assert candidates[0].generation.extra_body == {"thinking": {"type": "disabled"}}
    assert (tmp_path / "global-budget.sqlite3").is_file()
    all_bytes = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert b"deepseek-candidate-test-sentinel" not in all_bytes
    assert b"raw-candidate-response-id" not in all_bytes
    asyncio.run(client.aclose())


def test_candidate_generation_rejects_relative_global_ledger_before_provider(
    tmp_path: Path,
) -> None:
    config = load_experiment_config(CONFIG)
    corpus = _corpus(
        tmp_path,
        config_sha256=canonical_sha256(config.model_dump(mode="json")),
    )
    approved = candidate_generation_manifest_sha256(CONFIG, corpus.root)
    request = CandidateGenerationRequest(
        CONFIG, corpus.root, tmp_path / "candidates", True, False, approved
    )
    environ = _environment(tmp_path)
    environ["SPANVOUCH_PHASE5_BUDGET_LEDGER_PATH"] = "relative.sqlite3"

    with pytest.raises(ProviderConfigurationError, match="budget ledger"):
        asyncio.run(run_candidate_generation(request, environ=environ))

    assert not request.output_dir.exists()


@pytest.mark.parametrize("approved", [None, "f" * 64])
def test_candidate_generation_rejects_approval_drift_before_live_state(
    tmp_path: Path, approved: str | None
) -> None:
    config = load_experiment_config(CONFIG)
    corpus = _corpus(tmp_path, config_sha256=canonical_sha256(config.model_dump(mode="json")))
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _completion(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    request = CandidateGenerationRequest(
        CONFIG, corpus.root, tmp_path / "candidates", True, False, approved
    )
    with pytest.raises(ProviderConfigurationError, match="approved|approval|manifest"):
        asyncio.run(run_candidate_generation(request, environ={}, deepseek_client=client))
    assert calls == 0
    assert not request.output_dir.exists()
    assert not (tmp_path / ".cache").exists()
    asyncio.run(client.aclose())


def test_candidate_generation_rejects_corpus_config_drift_before_credentials(
    tmp_path: Path,
) -> None:
    corpus = _corpus(tmp_path, config_sha256="0" * 64)
    request = CandidateGenerationRequest(
        CONFIG, corpus.root, tmp_path / "candidates", True, False, "f" * 64
    )
    with pytest.raises(ValueError, match="configuration"):
        asyncio.run(run_candidate_generation(request, environ={}))
    assert not request.output_dir.exists()


def test_candidate_generation_resume_calls_provider_only_for_missing_cells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_experiment_config(CONFIG)
    corpus = _corpus(
        tmp_path,
        config_sha256=canonical_sha256(config.model_dump(mode="json")),
        record_count=2,
    )
    approved = candidate_generation_manifest_sha256(CONFIG, corpus.root)
    output = tmp_path / "candidates"
    asyncio.run(_freeze_first_candidate(corpus, output))
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _completion(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.chdir(tmp_path)
    request = CandidateGenerationRequest(
        config=CONFIG,
        corpus_dir=corpus.root,
        output_dir=output,
        allow_live_provider=True,
        formal_run=False,
        approved_manifest_sha256=approved,
        resume=True,
    )

    asyncio.run(
        run_candidate_generation(
            request,
            environ=_environment(tmp_path),
            deepseek_client=client,
        )
    )

    candidates = _load_candidates(output, corpus.verify().entries, corpus.manifest_sha256)
    assert len(candidates) == 2
    assert calls == 1
    asyncio.run(client.aclose())


def test_complete_resume_returns_before_credentials_or_provider_state(
    tmp_path: Path,
) -> None:
    config = load_experiment_config(CONFIG)
    corpus = _corpus(
        tmp_path,
        config_sha256=canonical_sha256(config.model_dump(mode="json")),
    )
    approved = candidate_generation_manifest_sha256(CONFIG, corpus.root)
    output = tmp_path / "candidates"
    asyncio.run(_freeze_first_candidate(corpus, output))
    request = CandidateGenerationRequest(
        config=CONFIG,
        corpus_dir=corpus.root,
        output_dir=output,
        allow_live_provider=False,
        formal_run=False,
        approved_manifest_sha256=approved,
        resume=True,
    )

    assert asyncio.run(run_candidate_generation(request, environ={})) == approved
    assert not (tmp_path / "global-budget.sqlite3").exists()


def test_existing_candidate_output_still_requires_explicit_resume(
    tmp_path: Path,
) -> None:
    config = load_experiment_config(CONFIG)
    corpus = _corpus(
        tmp_path,
        config_sha256=canonical_sha256(config.model_dump(mode="json")),
    )
    approved = candidate_generation_manifest_sha256(CONFIG, corpus.root)
    output = tmp_path / "candidates"
    asyncio.run(_freeze_first_candidate(corpus, output))
    request = CandidateGenerationRequest(
        config=CONFIG,
        corpus_dir=corpus.root,
        output_dir=output,
        allow_live_provider=True,
        formal_run=False,
        approved_manifest_sha256=approved,
    )

    with pytest.raises(FileExistsError, match="output"):
        asyncio.run(run_candidate_generation(request, environ={}))


def test_resume_rejects_extra_cells_before_live_state(tmp_path: Path) -> None:
    config = load_experiment_config(CONFIG)
    corpus = _corpus(
        tmp_path,
        config_sha256=canonical_sha256(config.model_dump(mode="json")),
    )
    approved = candidate_generation_manifest_sha256(CONFIG, corpus.root)
    output = tmp_path / "candidates"
    asyncio.run(_freeze_first_candidate(corpus, output))
    (output / "cells/unknown-cell").mkdir()
    request = CandidateGenerationRequest(
        config=CONFIG,
        corpus_dir=corpus.root,
        output_dir=output,
        allow_live_provider=True,
        formal_run=False,
        approved_manifest_sha256=approved,
        resume=True,
    )

    with pytest.raises(ValueError, match="layout|unknown"):
        asyncio.run(run_candidate_generation(request, environ={}))

    assert not (tmp_path / "global-budget.sqlite3").exists()
    assert not (tmp_path / ".cache").exists()


def test_resume_flag_does_not_change_manifest_only_identity(tmp_path: Path) -> None:
    config = load_experiment_config(CONFIG)
    corpus = _corpus(
        tmp_path,
        config_sha256=canonical_sha256(config.model_dump(mode="json")),
    )
    expected = candidate_generation_manifest_sha256(CONFIG, corpus.root)

    arguments = build_parser().parse_args(
        [
            "--config",
            str(CONFIG),
            "--corpus-dir",
            str(corpus.root),
            "--output-dir",
            str(tmp_path / "unused"),
            "--approved-manifest-sha256",
            expected,
            "--manifest-only",
            "--resume",
        ]
    )

    assert arguments.manifest_only is True
    assert arguments.resume is True
    assert candidate_generation_manifest_sha256(arguments.config, arguments.corpus_dir) == expected
