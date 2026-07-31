from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import Any, Self, cast

from fastapi.testclient import TestClient

from spanvouch.adapters.storage.sqlite_schema import initialize_database
from spanvouch.api.app import create_app
from spanvouch.diagnosis.engine import DiagnosisEngine
from spanvouch.projects.repository import ProjectRepository
from spanvouch.review.application import ReviewApplication
from spanvouch.review.protocols import ReviewRepository
from spanvouch.security.identity import Role
from spanvouch.trace.repository import InMemoryTraceRepository, TraceRepository

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class ProjectApiContext:
    client: AuthenticatedClient
    headers: dict[str, str]
    repository: ProjectRepository
    project_id: str


@dataclass(frozen=True)
class AdminApiContext:
    client: AuthenticatedClient
    headers: dict[str, str]
    repository: ProjectRepository


class AuthenticatedClient:
    def __init__(self, client: TestClient, headers: dict[str, str]) -> None:
        self._client = client
        self.headers = headers

    def __enter__(self) -> Self:
        self._client.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._client.__exit__(exc_type, exc, traceback)

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("headers", self.headers)
        return self._client.request(method, url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("headers", self.headers)
        return self._client.get(url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("headers", self.headers)
        return self._client.post(url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("headers", self.headers)
        return self._client.put(url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("headers", self.headers)
        return self._client.patch(url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("headers", self.headers)
        return self._client.delete(url, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def make_project_client(
    *,
    database: Path | None = None,
    trace_repository: TraceRepository | None = None,
    diagnosis_service: object | None = None,
    review_repository: object | None = None,
    review_service: object | None = None,
    project_name: str = "Alpha",
    project_roles: tuple[Role, ...] = (Role.OPERATOR, Role.REVIEWER),
) -> ProjectApiContext:
    if database is None:
        tempdir = TemporaryDirectory()
        db_path = Path(tempdir.name) / "spanvouch.db"
    else:
        tempdir = None
        db_path = database
    initialize_database(db_path)
    repository = ProjectRepository(db_path)
    if tempdir is not None:
        repository._codex_tempdir = tempdir  # type: ignore[attr-defined]
    project = next(
        (project for project in repository.list_projects() if project.name == project_name),
        None,
    )
    if project is None:
        project = repository.create_project(project_name, now=NOW)
    _, plaintext = repository.create_key(
        project.project_id,
        project_roles,
        now=NOW,
        expires_at=None,
    )
    client = TestClient(
        create_app(
            trace_repository=trace_repository or InMemoryTraceRepository(),
            diagnosis_service=cast(DiagnosisEngine | None, diagnosis_service),
            review_repository=cast(ReviewRepository | None, review_repository),
            review_service=cast(ReviewApplication | None, review_service),
            review_database=db_path,
            project_repository=repository,
        )
    )
    if tempdir is not None:
        client._codex_tempdir = tempdir
    return ProjectApiContext(
        client=AuthenticatedClient(client, {"Authorization": f"Bearer {plaintext}"}),
        headers={"Authorization": f"Bearer {plaintext}"},
        repository=repository,
        project_id=project.project_id,
    )


def make_admin_client(
    *,
    database: Path | None = None,
    trace_repository: TraceRepository | None = None,
    diagnosis_service: object | None = None,
    review_repository: object | None = None,
    review_service: object | None = None,
) -> AdminApiContext:
    if database is None:
        tempdir = TemporaryDirectory()
        db_path = Path(tempdir.name) / "spanvouch.db"
    else:
        tempdir = None
        db_path = database
    initialize_database(db_path)
    repository = ProjectRepository(db_path)
    if tempdir is not None:
        repository._codex_tempdir = tempdir  # type: ignore[attr-defined]
    _, plaintext = repository.create_key(
        None,
        (Role.ADMIN,),
        now=NOW,
        expires_at=None,
    )
    client = TestClient(
        create_app(
            trace_repository=trace_repository or InMemoryTraceRepository(),
            diagnosis_service=cast(DiagnosisEngine | None, diagnosis_service),
            review_repository=cast(ReviewRepository | None, review_repository),
            review_service=cast(ReviewApplication | None, review_service),
            review_database=db_path,
            project_repository=repository,
        )
    )
    if tempdir is not None:
        client._codex_tempdir = tempdir
    return AdminApiContext(
        client=AuthenticatedClient(client, {"Authorization": f"Bearer {plaintext}"}),
        headers={"Authorization": f"Bearer {plaintext}"},
        repository=repository,
    )
