class DiagnosisError(RuntimeError):
    """Base class for stable diagnosis service errors."""


class DiagnosisConflictError(DiagnosisError):
    """An idempotency key was reused for different diagnosis input."""


class DiagnosisUnavailableError(DiagnosisError):
    """The requested diagnoser is not configured."""
