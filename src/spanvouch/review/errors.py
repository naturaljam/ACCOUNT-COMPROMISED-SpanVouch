class ReviewError(Exception):
    """Base class for sanitized review persistence failures."""


class ReviewSchemaError(ReviewError):
    """The review database schema cannot be used by this application."""


class ReviewNotFoundError(ReviewError):
    """A requested review aggregate does not exist."""


class ReviewConflictError(ReviewError):
    """A review command conflicts with durable state."""


class ReviewValidationError(ReviewError):
    """Caller-supplied review content failed semantic validation."""


class ReviewPersistenceError(ReviewError):
    """SQLite could not complete a review operation."""


class ReviewWorkflowProviderError(ReviewError):
    """Sanitized provider failure raised only after durable human routing."""

    def __init__(self, case_id: str, code: str, *, retryable: bool) -> None:
        super().__init__(f"review provider failed: {code}")
        self.case_id = case_id
        self.code = code
        self.retryable = retryable
