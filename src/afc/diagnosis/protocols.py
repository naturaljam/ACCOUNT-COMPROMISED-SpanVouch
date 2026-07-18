from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from afc.diagnosis.evidence import EvidenceCatalog
from afc.diagnosis.models import DiagnosisExecution, ProviderUsage
from afc.diagnosis.trace_view import DiagnosticTraceView


class Diagnoser(Protocol):
    version_fingerprint: str

    async def diagnose(
        self, view: DiagnosticTraceView, evidence: EvidenceCatalog
    ) -> DiagnosisExecution:
        raise NotImplementedError


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class GenerationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = "deepseek-v4-flash"
    max_tokens: int = Field(default=1200, ge=1, le=4096)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)


class ProviderResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str
    model: str
    response_id: str
    finish_reason: str
    usage: ProviderUsage


class ModelProvider(Protocol):
    async def complete(
        self,
        messages: tuple[ChatMessage, ...],
        config: GenerationConfig,
    ) -> ProviderResponse:
        raise NotImplementedError
