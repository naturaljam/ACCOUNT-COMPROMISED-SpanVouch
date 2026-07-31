from __future__ import annotations

from enum import StrEnum

from spanvouch.security.identity import Principal, Role


class AuthorizationError(PermissionError):
    """Raised when an authenticated principal lacks a required capability."""


class Capability(StrEnum):
    READ_PROJECT = "read_project"
    INGEST_TRACE = "ingest_trace"
    RUN_DIAGNOSIS = "run_diagnosis"
    CREATE_REVIEW = "create_review"
    RESUME_REVIEW = "resume_review"
    DECIDE_REVIEW = "decide_review"
    MANAGE_PROJECTS = "manage_projects"
    MANAGE_KEYS = "manage_keys"
    EXPORT_AUDIT = "export_audit"


_ROLE_CAPABILITIES: dict[Role, frozenset[Capability]] = {
    Role.ADMIN: frozenset(Capability),
    Role.OPERATOR: frozenset(
        {
            Capability.READ_PROJECT,
            Capability.INGEST_TRACE,
            Capability.RUN_DIAGNOSIS,
            Capability.CREATE_REVIEW,
            Capability.RESUME_REVIEW,
        }
    ),
    Role.REVIEWER: frozenset({Capability.READ_PROJECT, Capability.DECIDE_REVIEW}),
    Role.VIEWER: frozenset({Capability.READ_PROJECT}),
}


class Policy:
    """Maps fixed v0.4 roles to capabilities."""

    @staticmethod
    def require(principal: Principal, capability: Capability) -> None:
        granted = frozenset().union(
            *(_ROLE_CAPABILITIES[role] for role in principal.roles)
        )
        if capability not in granted:
            raise AuthorizationError(f"principal is not authorized for {capability.value}")
