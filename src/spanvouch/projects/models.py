from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from spanvouch.security.identity import Principal


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("project_id is required")
        if not self.name:
            raise ValueError("project name is required")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")


@dataclass(frozen=True)
class ProjectScopedResourceId:
    project_id: str
    resource_id: str

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("project_id is required")
        if not self.resource_id:
            raise ValueError("resource_id is required")


@dataclass(frozen=True)
class ProjectContext:
    project_id: str
    principal: Principal

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("project_id is required")
