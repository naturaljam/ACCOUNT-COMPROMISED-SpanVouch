class DiagnosisError(RuntimeError):
    """Base class for stable diagnosis service errors."""


class DiagnosisConflictError(DiagnosisError):
    """An idempotency key was reused for different diagnosis input."""


class DiagnosisUnavailableError(DiagnosisError):
    """The requested diagnoser is not configured."""


class ProviderError(DiagnosisError):
    """Base class for model provider failures."""


class ProviderConfigurationError(ProviderError):
    """Provider configuration is absent or invalid."""


class ProviderProtocolError(ProviderError):
    """Provider returned a malformed success response."""


class ProviderRequestError(ProviderError):
    def __init__(
        self, code: str, *, status_code: int | None = None, retryable: bool = False
    ) -> None:
        super().__init__(f"provider request failed: {code}")
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
