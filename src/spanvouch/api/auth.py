from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import NoReturn, cast

from fastapi import Header, HTTPException, Request, status

from spanvouch.projects.models import ProjectContext
from spanvouch.projects.repository import ProjectRepository
from spanvouch.security.identity import (
    AuthenticationError,
    Principal,
    parse_bearer_token,
)
from spanvouch.security.policy import AuthorizationError, Capability, Policy


def get_principal(
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
        return repository.authenticate(token, now=_request_time(request))
    except AuthenticationError:
        _raise_authentication("authentication_failed")


def require_capability(capability: Capability) -> Callable[..., Principal]:
    def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
        selected_project: str | None = Header(default=None, alias="X-SpanVouch-Project"),
    ) -> Principal:
        principal = get_principal(
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


def require_project_capability(capability: Capability) -> Callable[..., ProjectContext]:
    def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
        selected_project: str | None = Header(default=None, alias="X-SpanVouch-Project"),
    ) -> ProjectContext:
        principal = get_principal(
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
        return ProjectContext(project_id=project_id, principal=principal)

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
