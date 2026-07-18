import json

import httpx
import pytest

from spanvouch.diagnosis.deepseek import DeepSeekConfig, DeepSeekProvider
from spanvouch.diagnosis.errors import (
    ProviderConfigurationError,
    ProviderProtocolError,
    ProviderRequestError,
)
from spanvouch.diagnosis.protocols import ChatMessage, GenerationConfig


def success_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "request-1",
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"status":"no_failure"}'},
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        },
    )


@pytest.mark.asyncio
async def test_provider_sends_bounded_json_request_without_exposing_key() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return success_response()

    config = DeepSeekConfig(api_key="super-secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await DeepSeekProvider(config, client=client).complete(
            (ChatMessage(role="user", content="Return json."),),
            GenerationConfig(),
        )

    assert seen["url"] == "https://api.deepseek.com/chat/completions"
    assert seen["authorization"] == "Bearer super-secret"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "deepseek-v4-flash"
    assert body["stream"] is False
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] == 1200
    assert response.content == '{"status":"no_failure"}'
    assert response.usage.total_tokens == 15
    assert "super-secret" not in repr(config)


@pytest.mark.asyncio
async def test_provider_retries_transient_status_once() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429) if attempts == 1 else success_response()

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await DeepSeekProvider(
            DeepSeekConfig(api_key="secret", backoff_seconds=0.01),
            client=client,
            sleeper=sleeper,
        ).complete((ChatMessage(role="user", content="json"),), GenerationConfig())

    assert attempts == 2
    assert sleeps == [0.01]


@pytest.mark.asyncio
async def test_provider_retries_timeout_once_then_raises_stable_error() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("sensitive upstream detail", request=request)

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderRequestError) as raised:
            await DeepSeekProvider(
                DeepSeekConfig(api_key="secret", backoff_seconds=0.01),
                client=client,
                sleeper=sleeper,
            ).complete((ChatMessage(role="user", content="json"),), GenerationConfig())

    assert attempts == 2
    assert sleeps == [0.01]
    assert raised.value.code == "transport_error"
    assert raised.value.retryable is True
    assert "sensitive upstream detail" not in str(raised.value)


@pytest.mark.parametrize("status", [400, 401, 402, 422])
@pytest.mark.asyncio
async def test_provider_does_not_retry_permanent_status(status: int) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status, text="body-must-not-leak")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderRequestError) as raised:
            await DeepSeekProvider(
                DeepSeekConfig(api_key="secret"), client=client
            ).complete((ChatMessage(role="user", content="json"),), GenerationConfig())

    assert attempts == 1
    assert raised.value.status_code == status
    assert "body-must-not-leak" not in str(raised.value)
    assert "secret" not in str(raised.value)


def test_config_requires_environment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(ProviderConfigurationError):
        DeepSeekConfig.from_env()


@pytest.mark.asyncio
async def test_provider_rejects_malformed_success_envelope() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    ) as client:
        with pytest.raises(ProviderProtocolError):
            await DeepSeekProvider(
                DeepSeekConfig(api_key="secret"), client=client
            ).complete((ChatMessage(role="user", content="json"),), GenerationConfig())
