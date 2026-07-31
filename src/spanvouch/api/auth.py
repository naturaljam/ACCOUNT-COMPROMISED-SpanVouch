from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import NoReturn, cast
from uuid import uuid4

from fastapi import Header, HTTPException, Request, status

from spanvouch.audit.context import AuditRequestContext, set_audit_context
from spanvouch.projects.context import set_project_context
from spanvouch.projects.models import ProjectContext
from spanvouch.projects.repository import ProjectRepository
from spanvouch.security.identity import (
    AuthenticationError,
    Principal,
    parse_bearer_token,
)
from spanvouch.security.policy import AuthorizationError, Capability, Policy


async def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    selected_project: str | None = Header(default=None, alias="X-SpanVouch-Project"),
) -> Principal:
    del selected_project
    if authorization is None:
        _raise_authentication("authentication_required")
    repository = _project_repository(request)
    try:
        token = parse_bearer_token(authorization)
        principal = await asyncio.to_thread(
            repository.authenticate,
            token,
            now=_request_time(request),
        )
    except AuthenticationError:
        _raise_authentication("authentication_failed")
    request_id = getattr(request.state, "audit_request_id", None)
    if not isinstance(request_id, str) or not request_id:
        request_id = uuid4().hex
        request.state.audit_request_id = request_id
    set_audit_context(
        AuditRequestContext(actor_key_id=principal.key_id, request_id=request_id)
    )
    return principal


def require_capability(capability: Capability) -> Callable[..., Awaitable[Principal]]:
    async def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
        selected_project: str | None = Header(default=None, alias="X-SpanVouch-Project"),
    ) -> Principal:
        principal = await get_principal(
            request,
            authorization=authorization,
            selected_project=selected_project,
        )
        try:
            Policy.require(principal, capability)
        except AuthorizationError as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "authorization_failed"},
            ) from error
        return principal

    return dependency


def require_project_capability(
    capability: Capability,
) -> Callable[..., Awaitable[ProjectContext]]:
    async def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
        selected_project: str | None = Header(default=None, alias="X-SpanVouch-Project"),
    ) -> ProjectContext:
        principal = await get_principal(
            request,
            authorization=authorization,
            selected_project=selected_project,
        )
        try:
            Policy.require(principal, capability)
        except AuthorizationError as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "authorization_failed"},
            ) from error
        project_id = _resolve_project_id(principal, selected_project)
        context = ProjectContext(project_id=project_id, principal=principal)
        set_project_context(context)
        return context

    return dependency


def _project_repository(request: Request) -> ProjectRepository:
    return cast(ProjectRepository, request.app.state.project_repository)


def _request_time(request: Request) -> datetime:
    clock = getattr(request.app.state, "clock", None)
    if callable(clock):
        return cast(datetime, clock())
    return datetime.now(UTC)


def _resolve_project_id(principal: Principal, selected_project: str | None) -> str:
    if principal.project_id is not None:
        if selected_project is not None and selected_project != principal.project_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "project_forbidden"},
            )
        return principal.project_id
    if selected_project is None or not selected_project.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "project_required"},
        )
    return selected_project


def _raise_authentication(code: str) -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code},
        headers={"WWW-Authenticate": "Bearer"},
    )
