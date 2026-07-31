from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from spanvouch.api.auth import require_capability
from spanvouch.projects.models import Project
from spanvouch.projects.repository import (
    ApiKeyConflictError,
    ApiKeyNotFoundError,
    AuditExportConflictError,
    AuditExportNotFoundError,
    AuditExportRecord,
    ProjectConflictError,
    ProjectNotFoundError,
    ProjectRepository,
)
from spanvouch.security.identity import ApiKeyRecord, Principal, Role
from spanvouch.security.policy import Capability

_REQUIRE_MANAGE_PROJECTS = require_capability(Capability.MANAGE_PROJECTS)
_REQUIRE_MANAGE_KEYS = require_capability(Capability.MANAGE_KEYS)
_REQUIRE_EXPORT_AUDIT = require_capability(Capability.EXPORT_AUDIT)


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


class ProjectResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    name: str
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    projects: tuple[ProjectResponse, ...]


class ApiKeyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: tuple[Role, ...] = Field(min_length=1)
    expires_at: datetime | None = None


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key_id: str
    prefix: str
    project_id: str | None
    roles: tuple[Role, ...]
    created_at: datetime
    expires_at: datetime | None
    api_key: str


class AuditExportResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    export_id: str
    project_id: str
    first_event_sequence: int
    last_event_sequence: int
    manifest_sha256: str
    bundle_path: str
    created_at: datetime


class AuditExportListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    exports: tuple[AuditExportResponse, ...]


def build_admin_router(project_repository: ProjectRepository) -> APIRouter:
    router = APIRouter(prefix="/v1/admin", tags=["admin"])

    @router.post(
        "/projects",
        response_model=ProjectResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_project(
        body: ProjectCreateRequest,
        request: Request,
        _: Annotated[Principal, Depends(_REQUIRE_MANAGE_PROJECTS)],
    ) -> ProjectResponse:
        try:
            project = project_repository.create_project(body.name, now=_request_time(request))
        except ProjectConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "project_conflict"},
            ) from error
        return _project_response(project)

    @router.get("/projects", response_model=ProjectListResponse)
    def list_projects(
        _: Annotated[Principal, Depends(_REQUIRE_MANAGE_PROJECTS)],
    ) -> ProjectListResponse:
        return ProjectListResponse(
            projects=tuple(
                _project_response(project)
                for project in project_repository.list_projects()
            )
        )

    @router.post(
        "/projects/{project_id}/api-keys",
        response_model=ApiKeyResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_api_key(
        project_id: str,
        body: ApiKeyCreateRequest,
        request: Request,
        _: Annotated[Principal, Depends(_REQUIRE_MANAGE_KEYS)],
    ) -> ApiKeyResponse:
        try:
            record, plaintext = project_repository.create_key(
                project_id,
                body.roles,
                now=_request_time(request),
                expires_at=body.expires_at,
            )
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "project_not_found"},
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "api_key_invalid"},
            ) from error
        return _api_key_response(record, plaintext)

    @router.post("/api-keys/{key_id}/rotate", response_model=ApiKeyResponse)
    def rotate_api_key(
        key_id: str,
        request: Request,
        _: Annotated[Principal, Depends(_REQUIRE_MANAGE_KEYS)],
    ) -> ApiKeyResponse:
        try:
            record, plaintext = project_repository.rotate_key(
                key_id, now=_request_time(request)
            )
        except ApiKeyNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "api_key_not_found"},
            ) from error
        except ApiKeyConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "api_key_conflict"},
            ) from error
        return _api_key_response(record, plaintext)

    @router.post("/api-keys/{key_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
    def revoke_api_key(
        key_id: str,
        request: Request,
        _: Annotated[Principal, Depends(_REQUIRE_MANAGE_KEYS)],
    ) -> Response:
        try:
            project_repository.revoke_key(key_id, now=_request_time(request))
        except ApiKeyNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "api_key_not_found"},
            ) from error
        except ApiKeyConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "api_key_conflict"},
            ) from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/audit-exports", response_model=AuditExportListResponse)
    def list_audit_exports(
        _: Annotated[Principal, Depends(_REQUIRE_EXPORT_AUDIT)],
    ) -> AuditExportListResponse:
        return AuditExportListResponse(
            exports=tuple(
                _audit_export_response(record)
                for record in project_repository.list_audit_exports()
            )
        )

    @router.post(
        "/projects/{project_id}/audit-exports",
        response_model=AuditExportResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_audit_export(
        project_id: str,
        request: Request,
        _: Annotated[Principal, Depends(_REQUIRE_EXPORT_AUDIT)],
    ) -> AuditExportResponse:
        signing_key_path = _audit_signing_key_path()
        try:
            record = project_repository.create_audit_export(
                project_id,
                _audit_export_root(),
                signing_key_path=signing_key_path,
                now=_request_time(request),
            )
        except ProjectNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "project_not_found"},
            ) from error
        except AuditExportConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "audit_export_conflict"},
            ) from error
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "audit_signing_key_invalid"},
            ) from error
        return _audit_export_response(record)

    @router.get("/audit-exports/{export_id}", response_model=AuditExportResponse)
    def get_audit_export(
        export_id: str,
        _: Annotated[Principal, Depends(_REQUIRE_EXPORT_AUDIT)],
    ) -> AuditExportResponse:
        try:
            return _audit_export_response(project_repository.get_audit_export(export_id))
        except AuditExportNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "audit_export_not_found"},
            ) from error

    return router


def _project_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        project_id=project.project_id,
        name=project.name,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _api_key_response(record: ApiKeyRecord, plaintext: str) -> ApiKeyResponse:
    return ApiKeyResponse(
        key_id=record.key_id,
        prefix=record.prefix,
        project_id=record.project_id,
        roles=record.roles,
        created_at=record.created_at,
        expires_at=record.expires_at,
        api_key=plaintext,
    )


def _audit_export_response(record: AuditExportRecord) -> AuditExportResponse:
    return AuditExportResponse(
        export_id=record.export_id,
        project_id=record.project_id,
        first_event_sequence=record.first_event_sequence,
        last_event_sequence=record.last_event_sequence,
        manifest_sha256=record.manifest_sha256,
        bundle_path=os.fspath(record.bundle_path),
        created_at=record.created_at,
    )


def _audit_signing_key_path() -> Path:
    value = os.environ.get("SPANVOUCH_AUDIT_SIGNING_KEY_PATH")
    if value is None or not value.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "audit_signing_key_required"},
        )
    return Path(value)


def _audit_export_root() -> Path:
    return Path(os.environ.get("SPANVOUCH_AUDIT_EXPORT_DIR", ".data/audit-exports"))


def _request_time(request: Request) -> datetime:
    clock = getattr(request.app.state, "clock", None)
    if callable(clock):
        return cast(datetime, clock())
    return datetime.now(UTC)
