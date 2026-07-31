from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class AuditRequestContext:
    actor_key_id: str
    request_id: str

    def __post_init__(self) -> None:
        if not self.actor_key_id:
            raise ValueError("actor_key_id is required")
        if not self.request_id:
            raise ValueError("request_id is required")


_CURRENT_AUDIT_CONTEXT: ContextVar[AuditRequestContext | None] = ContextVar(
    "spanvouch_audit_context",
    default=None,
)


def current_audit_context() -> AuditRequestContext | None:
    return _CURRENT_AUDIT_CONTEXT.get()


def set_audit_context(context: AuditRequestContext) -> Token[AuditRequestContext | None]:
    return _CURRENT_AUDIT_CONTEXT.set(context)


def reset_audit_context(token: Token[AuditRequestContext | None]) -> None:
    _CURRENT_AUDIT_CONTEXT.reset(token)


@contextmanager
def audit_context(context: AuditRequestContext) -> Iterator[AuditRequestContext]:
    token = set_audit_context(context)
    try:
        yield context
    finally:
        reset_audit_context(token)
