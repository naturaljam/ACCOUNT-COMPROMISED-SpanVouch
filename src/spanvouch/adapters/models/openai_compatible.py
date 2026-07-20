"""Pinned OpenAI-compatible provider used for the isolated Qwen verifier."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Literal, Self
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from spanvouch.contracts.diagnosis import ProviderUsage
from spanvouch.diagnosis.errors import (
    ProviderConfigurationError,
    ProviderProtocolError,
    ProviderRequestError,
)
from spanvouch.diagnosis.protocols import ChatMessage, GenerationConfig, ProviderResponse

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._+-]{1,64}$")


class OpenAICompatibleConfig(BaseModel):
    """Connection and immutable deployment identity for one OpenAI API root."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: SecretStr
    base_url: str
    expected_model: str = Field(min_length=1)
    endpoint_class: str = Field(min_length=1)
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    read_timeout_seconds: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=1, ge=0, le=1)
    backoff_seconds: float = Field(default=0.25, ge=0)
    smoke_only: bool = False
    container_repo_digest: str | None = None
    hf_revision: str | None = None

    @field_validator("base_url")
    @classmethod
    def validate_api_root(cls, value: str) -> str:
        normalized = value.rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an HTTP(S) URL")
        if parsed.query or parsed.fragment or parsed.path != "/v1":
            raise ValueError("base_url must be the API root ending in exactly /v1")
        return normalized

    @field_validator("container_repo_digest")
    @classmethod
    def validate_repo_digest(cls, value: str | None) -> str | None:
        if value is not None and _DIGEST_PATTERN.fullmatch(value) is None:
            raise ValueError("container_repo_digest must be an immutable sha256 RepoDigest")
        return value

    @field_validator("hf_revision")
    @classmethod
    def validate_hf_revision(cls, value: str | None) -> str | None:
        if value is not None and _REVISION_PATTERN.fullmatch(value) is None:
            raise ValueError("hf_revision must be an immutable 40-character commit revision")
        return value

    def validate_for_experiment(self, mode: Literal["pilot", "formal"]) -> Self:
        """Reject smoke or floating deployments before a paid experiment."""
        del mode  # both evidence-producing modes intentionally share this gate
        if self.smoke_only:
            raise ProviderConfigurationError("smoke-only endpoint cannot run an experiment")
        if self.container_repo_digest is None or self.hf_revision is None:
            raise ProviderConfigurationError("experiment endpoint is not immutably pinned")
        return self


class ServedModelProvenance(BaseModel):
    """Allowlisted, credential-free deployment provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint_class: str
    model: str
    server_version: str | None = None
    container_repo_digest: str | None = None
    hf_revision: str | None = None


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


class _CompletionEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    model: str
    choices: tuple[_Choice, ...] = Field(min_length=1)
    usage: _Usage


class _ModelEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1)


class _ModelList(BaseModel):
    model_config = ConfigDict(extra="ignore")
    data: tuple[_ModelEntry, ...] = Field(min_length=1)


class OpenAICompatibleProvider:
    """Minimal non-streaming Chat Completions client with bounded retry semantics."""

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._config = config
        self._client = client
        self._sleeper = sleeper

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self._config.connect_timeout_seconds,
            read=self._config.read_timeout_seconds,
            write=10.0,
            pool=5.0,
        )

    async def complete(
        self,
        messages: tuple[ChatMessage, ...],
        config: GenerationConfig,
    ) -> ProviderResponse:
        if config.model != self._config.expected_model:
            raise ProviderConfigurationError("generation model does not match expected model")
        if self._client is not None:
            return await self._complete_with_client(self._client, messages, config)
        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            return await self._complete_with_client(client, messages, config)

    async def _complete_with_client(
        self,
        client: httpx.AsyncClient,
        messages: tuple[ChatMessage, ...],
        generation: GenerationConfig,
    ) -> ProviderResponse:
        body = {
            "model": generation.model,
            "messages": [message.model_dump() for message in messages],
            "stream": False,
            "response_format": {"type": "json_object"},
            "max_tokens": generation.max_tokens,
            "temperature": generation.temperature,
            **generation.model_dump(mode="json")["extra_body"],
        }
        started = perf_counter()
        response = await self._request(
            client,
            "POST",
            f"{self._config.base_url}/chat/completions",
            json=body,
        )
        try:
            envelope = _CompletionEnvelope.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ProviderProtocolError("provider returned invalid success envelope") from exc
        if envelope.model != self._config.expected_model:
            raise ProviderProtocolError("provider returned an unexpected model")
        choice = envelope.choices[0]
        return ProviderResponse(
            content=choice.message.content,
            model=envelope.model,
            response_id=envelope.id,
            finish_reason=choice.finish_reason,
            usage=ProviderUsage(
                input_tokens=envelope.usage.prompt_tokens,
                output_tokens=envelope.usage.completion_tokens,
                total_tokens=envelope.usage.total_tokens,
                latency_ms=(perf_counter() - started) * 1000,
                request_id=envelope.id,
            ),
        )

    async def validate_served_model(self) -> ServedModelProvenance:
        if self._client is not None:
            return await self._validate_with_client(self._client)
        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            return await self._validate_with_client(client)

    async def _validate_with_client(
        self, client: httpx.AsyncClient
    ) -> ServedModelProvenance:
        response = await self._request(
            client,
            "GET",
            f"{self._config.base_url}/models",
        )
        try:
            model_list = _ModelList.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ProviderProtocolError("provider returned invalid model list") from exc
        if self._config.expected_model not in {item.id for item in model_list.data}:
            raise ProviderProtocolError("configured model is not served")
        raw_version = response.headers.get("x-vllm-version")
        server_version = (
            raw_version
            if raw_version is not None and _VERSION_PATTERN.fullmatch(raw_version)
            else None
        )
        return ServedModelProvenance(
            endpoint_class=self._config.endpoint_class,
            model=self._config.expected_model,
            server_version=server_version,
            container_repo_digest=self._config.container_repo_digest,
            hf_revision=self._config.hf_revision,
        )

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: Literal["GET", "POST"],
        url: str,
        *,
        json: object | None = None,
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._config.api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        for attempt in range(self._config.max_retries + 1):
            try:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    json=json,
                    timeout=self._timeout(),
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < self._config.max_retries:
                    await self._sleeper(self._config.backoff_seconds * (2**attempt))
                    continue
                raise ProviderRequestError("transport_error", retryable=True) from exc
            if response.status_code < 400:
                return response
            retryable = response.status_code == 429 or 500 <= response.status_code <= 599
            if retryable and attempt < self._config.max_retries:
                await self._sleeper(self._config.backoff_seconds * (2**attempt))
                continue
            raise ProviderRequestError(
                "upstream_http_error",
                status_code=response.status_code,
                retryable=retryable,
            )
        raise ProviderRequestError("missing_response", retryable=True)


async def validate_served_model(
    config: OpenAICompatibleConfig,
    *,
    client: httpx.AsyncClient | None = None,
) -> ServedModelProvenance:
    """Validate and summarize a served model without retaining response data."""
    return await OpenAICompatibleProvider(config, client=client).validate_served_model()
