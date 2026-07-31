from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.corpus import CorpusManifestMetadata, TraceReplayRepository
from spanvouch.evaluation.experiments.config import load_experiment_config
from spanvouch.evaluation.experiments.provider import ProviderConfigurationError
from spanvouch.evaluation.run_phase5_candidates import (
    CandidateGenerationRequest,
    candidate_generation_manifest_sha256,
    run_candidate_generation,
)
from spanvouch.evaluation.run_phase5_matrix import _load_candidates
from tests.evaluation.corpus.conftest import make_record

CONFIG = Path("evals/configs/phase5-pilot.json").resolve()
DEEPSEEK_PRICING = CONFIG.with_name("phase5-deepseek-v4-flash-pricing.json")


def _corpus(tmp_path: Path, *, config_sha256: str) -> TraceReplayRepository:
    record = make_record(repetition=1, seed=20260719)
    return TraceReplayRepository.freeze(
        records=(record,),
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
            expected_cell_count=1,
            expected_pair_count=0,
            created_at_utc=datetime(2026, 7, 20, tzinfo=UTC),
            parity_results_sha256=canonical_sha256([]),
        ),
    )


def _pricing(path: Path) -> Path:
    path.write_bytes(DEEPSEEK_PRICING.read_bytes())
    return path


def _environment(tmp_path: Path) -> dict[str, str]:
    deepseek_key_name = "DEEPSEEK" + "_API_KEY"
    return {
        deepseek_key_name: "deepseek-candidate-test-sentinel",
        "SPANVOUCH_PHASE5_BUDGET_LEDGER_PATH": str(
            (tmp_path / "global-budget.sqlite3").resolve()
        ),
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
        asyncio.run(
            run_candidate_generation(request, environ={}, deepseek_client=client)
        )
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
