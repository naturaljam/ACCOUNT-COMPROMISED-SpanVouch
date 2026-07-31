import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from spanvouch.adapters.models.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from spanvouch.diagnosis.errors import (
    ProviderConfigurationError,
    ProviderProtocolError,
    ProviderRequestError,
)
from spanvouch.diagnosis.protocols import ChatMessage, GenerationConfig


def _success(*, model: str = "qwen3.7-plus") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "qwen-request-1",
            "model": model,
            "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        },
    )


def _config(**updates: object) -> OpenAICompatibleConfig:
    values: dict[str, object] = {
        "api_key": "local-secret",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/",
        "expected_model": "qwen3.7-plus",
        "endpoint_class": "chat-completions",
        "service_operator": "alibaba-cloud-model-studio",
        "deployment_type": "managed-api",
    }
    values.update(updates)
    return OpenAICompatibleConfig.model_validate(values)


@pytest.mark.asyncio
async def test_provider_sends_exact_openai_request_and_parses_usage() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return _success()

    generation = GenerationConfig(
        model="qwen3.7-plus",
        max_tokens=321,
        temperature=0.2,
        extra_body={"enable_thinking": False},
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAICompatibleProvider(_config(), client=client).complete(
            (ChatMessage(role="user", content="Return JSON."),), generation
        )

    assert seen == {
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "auth": "Bearer local-secret",
        "body": {
            "model": "qwen3.7-plus",
            "messages": [{"role": "user", "content": "Return JSON."}],
            "stream": False,
            "response_format": {"type": "json_object"},
            "max_tokens": 321,
            "temperature": 0.2,
            "enable_thinking": False,
        },
    }
    assert "evaluator" not in json.dumps(seen["body"]).lower()
    assert result.model == "qwen3.7-plus"
    assert result.content == '{"ok":true}'
    assert result.usage.input_tokens == 7
    assert result.usage.output_tokens == 3
    assert result.usage.total_tokens == 10


@pytest.mark.asyncio
async def test_provider_revalidates_generation_before_merging_extra_body() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _success()

    forged = GenerationConfig(model="qwen3.7-plus").model_copy(
        update={"extra_body": {"model": "attacker/model"}}
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderConfigurationError, match="generation configuration"):
            await OpenAICompatibleProvider(_config(), client=client).complete(
                (ChatMessage(role="user", content="Return JSON."),), forged
            )

    assert calls == 0


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8000/compatible-mode/v2",
        "http://127.0.0.1:8000/v1/chat",
        "ftp://127.0.0.1/v1",
    ],
)
def test_config_requires_single_v1_api_root(base_url: str) -> None:
    with pytest.raises(ValidationError):
        _config(base_url=base_url)


def test_config_rejects_url_userinfo_without_leaking_password() -> None:
    with pytest.raises(ValidationError) as raised:
        _config(
            base_url="https://operator:do-not-leak@example.test/v1",
        )

    assert "do-not-leak" not in str(raised.value)
    assert "do-not-leak" not in repr(raised.value)


@pytest.mark.parametrize(
    "digest",
    [
        f"sha256:{'a' * 64}",
        "vllm/vllm-openai:latest",
        f"other/image@sha256:{'a' * 64}",
        f"vllm/vllm-openai@sha256:{'A' * 64}",
    ],
)
def test_config_rejects_noncanonical_vllm_repo_digest(digest: str) -> None:
    with pytest.raises(ValidationError, match="RepoDigest"):
        _config(container_repo_digest=digest)


@pytest.mark.parametrize(
    "reserved", ["model", "messages", "stream", "response_format", "max_tokens", "temperature"]
)
def test_generation_rejects_every_reserved_extra_body_key(reserved: str) -> None:
    with pytest.raises(ValidationError, match="reserved"):
        GenerationConfig(extra_body={reserved: "collision"})


def test_generation_extra_body_is_deeply_immutable_and_copied() -> None:
    source = {"nested": {"items": [1, 2]}}
    generation = GenerationConfig(extra_body=source)
    source["nested"]["items"].append(3)  # type: ignore[index, union-attr]

    assert generation.model_dump(mode="json")["extra_body"] == {"nested": {"items": [1, 2]}}
    with pytest.raises(TypeError, match="immutable"):
        generation.extra_body["nested"]["items"].append(4)  # type: ignore[index, union-attr]


@pytest.mark.asyncio
async def test_empty_extra_body_does_not_change_deepseek_payload_shape() -> None:
    from spanvouch.adapters.models.deepseek import DeepSeekConfig, DeepSeekProvider

    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(
            200,
            json={
                "id": "request-1",
                "model": "deepseek-v4-flash",
                "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await DeepSeekProvider(DeepSeekConfig(api_key="secret"), client=client).complete(
            (ChatMessage(role="user", content="json"),), GenerationConfig(extra_body={})
        )

    assert json.loads(bodies[0]) == {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "json"}],
        "stream": False,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "max_tokens": 1200,
        "temperature": 0.0,
    }


@pytest.mark.parametrize("status", [429, 500, 501, 503, 599])
@pytest.mark.asyncio
async def test_provider_retries_every_transient_status_once(status: int) -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status) if attempts == 1 else _success()

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await OpenAICompatibleProvider(
            _config(backoff_seconds=0.01), client=client, sleeper=sleeper
        ).complete(
            (ChatMessage(role="user", content="json"),),
            GenerationConfig(model="qwen3.7-plus"),
        )
    assert attempts == 2
    assert sleeps == [0.01]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
@pytest.mark.asyncio
async def test_provider_does_not_retry_other_4xx_or_leak_body(status: int) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status, text="secret response")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderRequestError) as raised:
            await OpenAICompatibleProvider(_config(), client=client).complete(
                (ChatMessage(role="user", content="json"),),
                GenerationConfig(model="qwen3.7-plus"),
            )
    assert attempts == 1
    assert raised.value.status_code == status
    assert "secret response" not in str(raised.value)
    assert "local-secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_provider_retries_transport_once_then_raises_sanitized_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadError("credential-like detail", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderRequestError, match="transport_error") as raised:
            await OpenAICompatibleProvider(
                _config(backoff_seconds=0), client=client
            ).complete(
                (ChatMessage(role="user", content="json"),),
                GenerationConfig(model="qwen3.7-plus"),
            )
    assert attempts == 2
    assert raised.value.retryable is True
    assert "credential-like" not in str(raised.value)


@pytest.mark.parametrize(
    "response_factory",
    [
        lambda: httpx.Response(200, content=b"not-json"),
        lambda: httpx.Response(200, json={}),
        lambda: httpx.Response(
            200,
            json={
                "id": "x",
                "model": "qwen3.7-plus",
                "choices": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        ),
        lambda: httpx.Response(
            200,
            json={
                "id": "x",
                "model": "qwen3.7-plus",
                "choices": [{"finish_reason": "stop", "message": {"content": None}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        ),
        lambda: _success(model="unexpected-model"),
    ],
)
@pytest.mark.asyncio
async def test_provider_rejects_invalid_or_wrong_model_envelope(
    response_factory: Callable[[], httpx.Response],
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: response_factory())
    ) as client:
        with pytest.raises(ProviderProtocolError):
            await OpenAICompatibleProvider(_config(), client=client).complete(
                (ChatMessage(role="user", content="json"),),
                GenerationConfig(model="qwen3.7-plus"),
            )


@pytest.mark.asyncio
async def test_validate_served_model_returns_only_allowlisted_provenance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://dashscope.aliyuncs.com/compatible-mode/v1/models"
        assert request.headers["Authorization"] == "Bearer local-secret"
        return httpx.Response(
            200,
            headers={"x-vllm-version": "0.9.2", "x-secret": "must-not-persist"},
            json={"object": "list", "data": [{"id": "qwen3.7-plus"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provenance = await OpenAICompatibleProvider(
            _config(), client=client
        ).validate_served_model()

    assert provenance.model_dump(mode="json") == {
        "service_operator": "alibaba-cloud-model-studio",
        "deployment_type": "managed-api",
        "endpoint_class": "chat-completions",
        "model": "qwen3.7-plus",
        "server_version": None,
    }
    assert "must-not-persist" not in repr(provenance)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": "not-a-list"},
        {"data": [{}]},
        {"data": [{"id": "other-model"}]},
    ],
)
@pytest.mark.asyncio
async def test_validate_served_model_rejects_malformed_or_mismatched_list(
    payload: dict[str, object],
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ProviderProtocolError):
            await OpenAICompatibleProvider(_config(), client=client).validate_served_model()


def test_pilot_and_formal_reject_smoke_or_self_hosted_deployment() -> None:
    smoke = _config(smoke_only=True)
    self_hosted = _config(
        deployment_type="self-hosted-vllm",
        container_repo_digest=f"vllm/vllm-openai@sha256:{'a' * 64}",
        hf_revision="b" * 40,
    )
    managed = _config()
    for mode in ("pilot", "formal"):
        with pytest.raises(ProviderConfigurationError):
            smoke.validate_for_experiment(mode)
        with pytest.raises(ProviderConfigurationError):
            self_hosted.validate_for_experiment(mode)
        assert managed.validate_for_experiment(mode) is managed


def test_config_and_errors_do_not_expose_credentials() -> None:
    config = _config()
    assert "local-secret" not in repr(config)
    assert "local-secret" not in str(config)


def test_checked_in_smoke_config_and_environment_contain_no_credentials() -> None:
    path = Path("evals/configs/phase5-qwen-managed.example.json")
    payload = json.loads(path.read_text())
    generation = GenerationConfig.model_validate(payload.pop("generation"))
    config = OpenAICompatibleConfig(api_key="", **payload)
    env = Path(".env.example").read_text()

    assert config.smoke_only is True
    assert generation.extra_body == {"enable_thinking": False}
    assert "SPANVOUCH_QWEN_BASE_URL=\n" in env
    assert "SPANVOUCH_QWEN_API_KEY=\n" in env
    assert "local-secret" not in path.read_text()
