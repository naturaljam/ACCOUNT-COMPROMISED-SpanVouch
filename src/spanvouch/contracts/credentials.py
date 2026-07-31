"""Shared detection of credential signatures that must never be persisted."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

__all__ = ["contains_credential_signature"]

_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?:api[_\s-]?key|access[_\s-]?key|authorization|authentication|"
    r"password|client[_\s-]?secret|session[_\s-]?token|credential)\s*(?:=|:)",
    re.IGNORECASE,
)
_AUTH_SCHEME = re.compile(r"\b(?:basic|bearer|token)\s+\S+", re.IGNORECASE)
_TOKEN_PREFIX = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"glpat-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{16,}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16})\b"
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_PEM_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")


def contains_credential_signature(value: str) -> bool:
    """Return whether text contains a non-bypassable credential signature."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        parsed = None
    if parsed is not None and (parsed.username is not None or parsed.password is not None):
        return True
    return bool(
        _CREDENTIAL_ASSIGNMENT.search(value)
        or _AUTH_SCHEME.search(value)
        or _TOKEN_PREFIX.search(value)
        or _JWT.search(value)
        or _PEM_PRIVATE_KEY.search(value)
    )
