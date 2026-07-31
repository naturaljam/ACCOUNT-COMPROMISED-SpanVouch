from __future__ import annotations

from contextvars import ContextVar, Token

from spanvouch.projects.models import ProjectContext

_CURRENT_PROJECT_CONTEXT: ContextVar[ProjectContext | None] = ContextVar(
    "spanvouch_project_context",
    default=None,
)


def current_project_context() -> ProjectContext | None:
    return _CURRENT_PROJECT_CONTEXT.get()


def set_project_context(context: ProjectContext) -> Token[ProjectContext | None]:
    return _CURRENT_PROJECT_CONTEXT.set(context)


def reset_project_context(token: Token[ProjectContext | None]) -> None:
    _CURRENT_PROJECT_CONTEXT.reset(token)
