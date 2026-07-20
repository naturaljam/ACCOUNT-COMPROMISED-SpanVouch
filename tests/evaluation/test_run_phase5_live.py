from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from spanvouch.evaluation import phase5_live_composition, run_phase5_matrix
from spanvouch.evaluation.experiments.budget import Pricing
from spanvouch.evaluation.experiments.config import (
    FormalFreezePolicy,
    freeze_formal_config,
    load_experiment_config,
)
from spanvouch.evaluation.experiments.provider import ProviderConfigurationError
from spanvouch.evaluation.experiments.runner import OutcomeStatus, ProviderPhaseRepository
from tests.evaluation.experiments.test_planner import _candidate_pair


def _pricing(path: Path, provider: str, model: str) -> Path:
    value = Pricing(
        provider=provider,
        model=model,
        currency="CNY",
        effective_date=date(2026, 7, 20),
        source_url="https://pricing.example.invalid/source",
        input_per_million=Decimal("1"),
        output_per_million=Decimal("2"),
        gpu_hourly=Decimal("0"),
        amounts="estimated",
    )
    path.write_text(value.model_dump_json(), encoding="utf-8")
    return path


def _environment(tmp_path: Path) -> dict[str, str]:
    deepseek_secret, qwen_secret = _sentinel_credentials()
    deepseek_key_name = "DEEPSEEK" + "_API_KEY"
    return {
        deepseek_key_name: deepseek_secret,
        "SPANVOUCH_VLLM_API_KEY": qwen_secret,
        "SPANVOUCH_VLLM_BASE_URL": "https://qwen.example.invalid/v1",
        "SPANVOUCH_VLLM_CONTAINER_REPO_DIGEST": "vllm/vllm-openai@sha256:" + "a" * 64,
        "SPANVOUCH_VLLM_HF_REVISION": "b" * 40,
        "SPANVOUCH_PHASE5_DEEPSEEK_PRICING_PATH": str(
            _pricing(tmp_path / "deepseek-price.json", "deepseek", "deepseek-v4-flash")
        ),
        "SPANVOUCH_PHASE5_QWEN_PRICING_PATH": str(
            _pricing(tmp_path / "qwen-price.json", "qwen", "Qwen/Qwen3-14B")
        ),
    }


def _sentinel_credentials() -> tuple[str, str]:
    return (
        "-".join(("deepseek", "test", "sentinel")),
        "-".join(("qwen", "test", "sentinel")),
    )


def _completion(request: httpx.Request) -> httpx.Response:
    model = json.loads(request.content)["model"]
    return httpx.Response(
        200,
        request=request,
        json={
            "id": "raw-provider-response-id",
            "model": model,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "verdict": "verified",
                                "findings": [],
                                "evidence_gaps": [],
                                "alternative_failure_type": None,
                                "confidence": 0.9,
                            }
                        )
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        },
    )


def test_pilot_live_cli_routes_deepseek_and_qwen_without_persisting_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(_candidate_pair(tmp_path / "fixture"))
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return _completion(request)

    deepseek_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    qwen_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config_path = Path("evals/configs/phase5-pilot.json").resolve()
    config = load_experiment_config(config_path)
    providers = phase5_live_composition._compose_live_providers(
        config,
        environ=_environment(tmp_path),
        deepseek_client=deepseek_client,
        qwen_client=qwen_client,
    )
    monkeypatch.setattr(
        phase5_live_composition,
        "_compose_live_providers",
        lambda config, **kwargs: providers,
    )
    approved_manifest = "a" * 64

    def approve_exact_manifest(
        request: run_phase5_matrix.ProviderRunRequest,
        *,
        matrix_manifest_sha256: str,
    ) -> str:
        assert request.approved_manifest_sha256 == approved_manifest
        return matrix_manifest_sha256

    monkeypatch.setattr(
        run_phase5_matrix, "_require_approved_manifest", approve_exact_manifest
    )
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "provider-results"
    assert run_phase5_matrix.main(
        [
            "--config", str(config_path),
            "--corpus-dir", str(tmp_path / "fixture/corpus"),
            "--candidate-dir", str(tmp_path / "fixture/candidates"),
            "--output-dir", str(output),
            "--allow-live-provider",
            "--approved-manifest-sha256", approved_manifest,
        ]
    ) == 0

    manifest = ProviderPhaseRepository(output).verify(
        expected_manifest_sha256=ProviderPhaseRepository(output).manifest_sha256
    )
    assert manifest.provider_phase_complete
    assert manifest.status_counts[OutcomeStatus.COMPLETED] == 12
    assert calls.count("api.deepseek.com") == 4
    assert calls.count("qwen.example.invalid") == 4
    all_bytes = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    deepseek_secret, qwen_secret = _sentinel_credentials()
    assert deepseek_secret.encode() not in all_bytes
    assert qwen_secret.encode() not in all_bytes
    assert b"raw-provider-response-id" not in all_bytes
    asyncio.run(deepseek_client.aclose())
    asyncio.run(qwen_client.aclose())


@pytest.mark.parametrize(
    "change",
    [
        lambda env: env.pop("DEEPSEEK" + "_API_KEY"),
        lambda env: env.update(SPANVOUCH_VLLM_BASE_URL="not-a-url"),
        lambda env: env.update(SPANVOUCH_PHASE5_QWEN_PRICING_PATH="missing.json"),
    ],
)
def test_live_composition_rejects_missing_env_endpoint_or_price_before_call(
    tmp_path: Path, change: object
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _completion(request)

    environ = _environment(tmp_path)
    change(environ)  # type: ignore[operator]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises((ProviderConfigurationError, ValueError)):
        phase5_live_composition._compose_live_providers(
            load_experiment_config(Path("evals/configs/phase5-pilot.json")),
            environ=environ,
            deepseek_client=client,
            qwen_client=client,
        )
    assert calls == 0
    asyncio.run(client.aclose())


def test_formal_live_cli_defers_to_paid_authorization_before_provider_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(_candidate_pair(tmp_path / "fixture"))
    pilot = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    policy = FormalFreezePolicy.model_validate_json(
        Path("evals/configs/phase5-formal-policy.json").read_text(encoding="utf-8")
    )
    formal = freeze_formal_config(
        pilot,
        policy,
        repetitions=policy.minimum_repetitions,
        coverage_loss_tolerance=0.05,
        frozen_at_utc=datetime(2026, 7, 20, tzinfo=UTC),
    )
    config_path = tmp_path / "formal.json"
    config_path.write_text(formal.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        run_phase5_matrix,
        "compose_live_executor",
        lambda *args, **kwargs: pytest.fail("provider env read before authorization"),
    )
    with pytest.raises(ProviderConfigurationError, match="formal live run"):
        run_phase5_matrix.main(
            [
                "--config", str(config_path),
                "--corpus-dir", str(tmp_path / "fixture/corpus"),
                "--candidate-dir", str(tmp_path / "fixture/candidates"),
                "--output-dir", str(tmp_path / "provider"),
                "--allow-live-provider",
                "--formal-run",
            ]
        )


def test_pilot_live_cli_defers_to_manifest_approval_before_provider_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(_candidate_pair(tmp_path / "fixture"))
    monkeypatch.setattr(
        run_phase5_matrix,
        "compose_live_executor",
        lambda *args, **kwargs: pytest.fail("provider env read before authorization"),
    )

    with pytest.raises(ProviderConfigurationError, match="live run requires"):
        run_phase5_matrix.main(
            [
                "--config", str(Path("evals/configs/phase5-pilot.json").resolve()),
                "--corpus-dir", str(tmp_path / "fixture/corpus"),
                "--candidate-dir", str(tmp_path / "fixture/candidates"),
                "--output-dir", str(tmp_path / "provider"),
                "--allow-live-provider",
            ]
        )
