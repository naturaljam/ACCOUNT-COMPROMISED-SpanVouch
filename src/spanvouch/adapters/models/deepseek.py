import asyncio
import os
from collections.abc import Awaitable, Callable
from time import perf_counter

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from spanvouch.contracts.diagnosis import ProviderUsage
from spanvouch.diagnosis.errors import (
    ProviderConfigurationError,
    ProviderProtocolError,
    ProviderRequestError,
)
from spanvouch.diagnosis.protocols import (
    ChatMessage,
    GenerationConfig,
    ProviderResponse,
)


class DeepSeekConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: SecretStr
    base_url: str = "https://api.deepseek.com"
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    read_timeout_seconds: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=1, ge=0, le=1)
    backoff_seconds: float = Field(default=0.25, ge=0)

    @classmethod
    def from_env(cls) -> "DeepSeekConfig":
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise ProviderConfigurationError("DEEPSEEK_API_KEY is not configured")
        return cls(api_key=SecretStr(key))


class _Usage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class _Message(BaseModel):
    model_config = ConfigDict(extra="ignore")
    content: str


class _Choice(BaseModel):
    model_config = ConfigDict(extra="ignore")
    finish_reason: str
    message: _Message


class _Envelope(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    model: str
    choices: tuple[_Choice, ...] = Field(min_length=1)
    usage: _Usage


class DeepSeekProvider:
    def __init__(
        self,
        config: DeepSeekConfig,
        *,
        client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._config = config
        self._client = client
        self._sleeper = sleeper

    async def complete(
        self,
        messages: tuple[ChatMessage, ...],
        config: GenerationConfig,
    ) -> ProviderResponse:
        timeout = httpx.Timeout(
            connect=self._config.connect_timeout_seconds,
            read=self._config.read_timeout_seconds,
            write=10.0,
            pool=5.0,
        )
        if self._client is not None:
            return await self._complete_with_client(self._client, messages, config, timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await self._complete_with_client(client, messages, config, timeout)

    async def _complete_with_client(
        self,
        client: httpx.AsyncClient,
        messages: tuple[ChatMessage, ...],
        generation: GenerationConfig,
        timeout: httpx.Timeout,
    ) -> ProviderResponse:
        body = {
            "model": generation.model,
            "messages": [message.model_dump() for message in messages],
            "stream": False,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "max_tokens": generation.max_tokens,
            "temperature": generation.temperature,
        }
        headers = {
            "Authorization": f"Bearer {self._config.api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        started = perf_counter()
        response: httpx.Response | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                response = await client.post(
                    f"{self._config.base_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=body,
                    timeout=timeout,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < self._config.max_retries:
                    await self._sleeper(self._config.backoff_seconds * (2**attempt))
                    continue
                raise ProviderRequestError("transport_error", retryable=True) from exc
            if response.status_code < 400:
                break
            retryable = response.status_code in {429, 500, 503}
            if retryable and attempt < self._config.max_retries:
                await self._sleeper(self._config.backoff_seconds * (2**attempt))
                continue
            raise ProviderRequestError(
                "upstream_http_error",
                status_code=response.status_code,
                retryable=retryable,
            )
        if response is None:
            raise ProviderRequestError("missing_response", retryable=True)
        try:
            envelope = _Envelope.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ProviderProtocolError("provider returned invalid success envelope") from exc
        if envelope.model != generation.model:
            raise ProviderProtocolError("provider returned unexpected model")
        choice = envelope.choices[0]
        elapsed_ms = (perf_counter() - started) * 1000
        return ProviderResponse(
            content=choice.message.content,
            model=envelope.model,
            response_id=envelope.id,
            finish_reason=choice.finish_reason,
            usage=ProviderUsage(
                input_tokens=envelope.usage.prompt_tokens,
                output_tokens=envelope.usage.completion_tokens,
                total_tokens=envelope.usage.total_tokens,
                latency_ms=elapsed_ms,
                request_id=envelope.id,
            ),
        )
