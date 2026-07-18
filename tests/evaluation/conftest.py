from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest

from spanvouch.contracts.artifacts import (
    ArtifactManifest,
    ArtifactRef,
    CodeProvenance,
    PackageProvenance,
    RuntimeProvenance,
)
from spanvouch.contracts.versioning import canonical_sha256


@pytest.fixture
def artifact_manifest() -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id="phase4-offline-reference",
        artifact_kind="evaluation_bundle",
        created_at_utc=datetime(2026, 7, 18, tzinfo=UTC),
        command_name="spanvouch evaluate review",
        code=CodeProvenance(
            git_commit="a" * 40,
            repository_identity="local:self-agent",
            dirty_worktree=False,
        ),
        package=PackageProvenance(name="spanvouch", version="0.2.0"),
        contracts={"spanvouch.verification": "1.0"},
        configuration=ArtifactRef(
            path="config.json",
            sha256=canonical_sha256({"mode": "deterministic"}),
            media_type="application/json",
        ),
        runtime=RuntimeProvenance(
            python="3.12.10",
            os="windows",
            architecture="amd64",
            dependency_lock_sha256="b" * 64,
        ),
        inputs=(
            ArtifactRef(
                path="config.json",
                sha256=canonical_sha256({"mode": "deterministic"}),
                media_type="application/json",
            ),
        ),
        outputs=(
            ArtifactRef(
                path="README.md",
                sha256=sha256(b"# Reproduce\n").hexdigest(),
                media_type="text/markdown",
            ),
            ArtifactRef(
                path="environment.txt",
                sha256=sha256(b"python=3.12\n").hexdigest(),
                media_type="text/plain",
            ),
            ArtifactRef(
                path="metrics.json",
                sha256=canonical_sha256({"status": "complete"}),
                media_type="application/json",
            ),
            ArtifactRef(
                path="structured-events.jsonl",
                sha256=sha256(b"").hexdigest(),
                media_type="application/x-ndjson",
            ),
        ),
        provider_status="not_used",
    )
