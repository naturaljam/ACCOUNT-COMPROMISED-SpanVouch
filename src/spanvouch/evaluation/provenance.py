"""Deterministic, injectable provenance and artifact bundle construction."""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import sys
import tempfile
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from spanvouch.contracts.artifacts import (
    ArtifactManifest,
    ArtifactRef,
    CodeProvenance,
    CostProvenance,
    DatasetProvenance,
    ModelProvenance,
    PackageProvenance,
    RandomnessProvenance,
    RuntimeProvenance,
    UsageProvenance,
)
from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.artifacts import (
    ArtifactBundleWriter,
    _publish_no_replace,
    collect_git_provenance,
)

_CREATED_AT = datetime(2026, 7, 18, tzinfo=UTC)
_CONTRACTS = {
    "spanvouch.artifact-manifest": "1.0",
    "spanvouch.diagnosis": "1.0",
    "spanvouch.diagnostic-context": "1.0",
    "spanvouch.review": "1.0",
    "spanvouch.trace": "1.0",
    "spanvouch.verification": "1.0",
}


class ProvenanceCollector(Protocol):
    def code(self) -> CodeProvenance: ...

    def runtime(self) -> RuntimeProvenance: ...


class ExecutionMetadata(BaseModel):
    """Actual provider execution observed by an evaluation command."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_status: Literal["not_used", "used", "failed"] = "not_used"
    models: tuple[ModelProvenance, ...] = ()
    usage: UsageProvenance | None = None
    cost: CostProvenance | None = None

    @model_validator(mode="after")
    def validate_status(self) -> ExecutionMetadata:
        if self.provider_status == "not_used" and (
            self.models or self.usage is not None or self.cost is not None
        ):
            raise ValueError("not_used execution forbids provider metadata")
        if self.provider_status == "used" and (not self.models or self.usage is None):
            raise ValueError("used execution requires models and usage")
        return self


@dataclass(frozen=True)
class _PublishedBundleIdentity:
    device: int
    inode: int
    tree_fingerprint: str


@dataclass(frozen=True)
class LocalProvenanceCollector:
    repository: Path

    def code(self) -> CodeProvenance:
        return collect_git_provenance(self.repository)

    def runtime(self) -> RuntimeProvenance:
        lock = self.repository / "uv.lock"
        return RuntimeProvenance(
            python=sys.version.split()[0],
            os=platform.system().lower(),
            architecture=platform.machine().lower(),
            dependency_lock_sha256=sha256(lock.read_bytes()).hexdigest(),
        )


def default_collector() -> ProvenanceCollector:
    return LocalProvenanceCollector(Path.cwd())


def manifest_path_for(output: Path, bundle_dir: Path | None = None) -> Path:
    destination = bundle_dir if bundle_dir is not None else Path(f"{output}.bundle")
    return destination / "manifest.json"


def require_release_eligible(collector: ProvenanceCollector, *, allow_dirty: bool) -> None:
    if collector.code().dirty_worktree and not allow_dirty:
        raise ValueError("release artifact requires a clean worktree")


def write_bound_bundle(
    *,
    output: Path,
    report: JsonValue,
    config: Mapping[str, JsonValue],
    command_name: str,
    artifact_kind: Literal["dataset_generation", "evaluation_bundle"],
    seed: int,
    datasets: tuple[DatasetProvenance, ...] = (),
    bundle_dir: Path | None = None,
    artifact_id: str | None = None,
    allow_dirty: bool = False,
    collector: ProvenanceCollector | None = None,
    execution: ExecutionMetadata | None = None,
) -> Path:
    """Bind the already-written output to a fail-closed Task 14 bundle."""
    source = collector or default_collector()
    actual_execution = execution or ExecutionMetadata()
    code = source.code()
    if code.dirty_worktree and not allow_dirty:
        raise ValueError("release artifact requires a clean worktree")
    destination = manifest_path_for(output, bundle_dir).parent
    config_value = dict(config)
    config_ref = ArtifactRef(
        path="config.json",
        sha256=canonical_sha256(cast(JsonValue, config_value)),
        media_type="application/json",
    )
    events = b""
    environment = _environment(code, source.runtime())
    readme = "# SpanVouch artifact\n\nOffline deterministic reproduction evidence.\n"
    outputs = (
        ArtifactRef(
            path="README.md",
            sha256=sha256(readme.encode("utf-8")).hexdigest(),
            media_type="text/markdown",
        ),
        ArtifactRef(
            path="environment.txt",
            sha256=sha256(environment.encode("utf-8")).hexdigest(),
            media_type="text/plain",
        ),
        ArtifactRef(
            path="metrics.json", sha256=canonical_sha256(report), media_type="application/json"
        ),
        ArtifactRef(
            path="structured-events.jsonl",
            sha256=sha256(events).hexdigest(),
            media_type="application/x-ndjson",
        ),
    )
    manifest = ArtifactManifest(
        artifact_id=artifact_id or output.stem,
        artifact_kind=artifact_kind,
        created_at_utc=_CREATED_AT,
        command_name=command_name,
        code=code,
        package=PackageProvenance(name="spanvouch", version="0.2.0"),
        contracts=_CONTRACTS,
        datasets=datasets,
        configuration=config_ref,
        randomness=RandomnessProvenance(seed=seed, deterministic_flags=("offline",)),
        runtime=source.runtime(),
        inputs=(config_ref,),
        outputs=outputs,
        models=actual_execution.models,
        usage=actual_execution.usage,
        cost=actual_execution.cost,
        provider_status=actual_execution.provider_status,
    )
    ArtifactBundleWriter(destination).write(
        manifest=manifest,
        config=cast(JsonValue, config_value),
        metrics=report,
        structured_events=(),
        environment=environment,
        readme=readme,
    )
    return destination


def publish_report_and_bundle(
    *,
    output: Path,
    render_report: Callable[[Path], None],
    config: Mapping[str, JsonValue],
    command_name: str,
    artifact_kind: Literal["dataset_generation", "evaluation_bundle"],
    seed: int,
    datasets: tuple[DatasetProvenance, ...] = (),
    bundle_dir: Path | None = None,
    artifact_id: str | None = None,
    allow_dirty: bool = False,
    collector: ProvenanceCollector | None = None,
    execution: ExecutionMetadata | None = None,
) -> Path:
    """Stage the report until its bundle has published successfully."""
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(dir=output.parent, prefix=f".{output.name}.tmp-")
    )
    temporary = staging / "report.json"
    staged_bundle = staging / "bundle"
    destination_bundle = manifest_path_for(output, bundle_dir).parent
    try:
        render_report(temporary)
        report = cast(JsonValue, json.loads(temporary.read_text(encoding="utf-8")))
        write_bound_bundle(
            output=output,
            report=report,
            config=config,
            command_name=command_name,
            artifact_kind=artifact_kind,
            seed=seed,
            datasets=datasets,
            bundle_dir=staged_bundle,
            artifact_id=artifact_id,
            allow_dirty=allow_dirty,
            collector=collector,
            execution=execution,
        )
        _publish_staged_pair(
            staged_primary=temporary,
            staged_bundle=staged_bundle,
            destination_primary=output,
            destination_bundle=destination_bundle,
        )
        return destination_bundle
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def publish_dataset_and_bundle(
    *,
    output: Path,
    build_dataset: Callable[[Path], JsonValue],
    config: Mapping[str, JsonValue],
    command_name: str,
    seed: int,
    datasets: tuple[DatasetProvenance, ...] = (),
    bundle_dir: Path | None = None,
    artifact_id: str | None = None,
    allow_dirty: bool = False,
    collector: ProvenanceCollector | None = None,
    execution: ExecutionMetadata | None = None,
) -> Path:
    """Build and publish a dataset directory and its bound bundle as one pair."""
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(dir=output.parent, prefix=f".{output.name}.tmp-")
    )
    staged_dataset = staging / "dataset"
    staged_bundle = staging / "bundle"
    destination_bundle = manifest_path_for(output, bundle_dir).parent
    try:
        report = build_dataset(staged_dataset)
        write_bound_bundle(
            output=output,
            report=report,
            config=config,
            command_name=command_name,
            artifact_kind="dataset_generation",
            seed=seed,
            datasets=datasets,
            bundle_dir=staged_bundle,
            artifact_id=artifact_id,
            allow_dirty=allow_dirty,
            collector=collector,
            execution=execution,
        )
        _publish_staged_pair(
            staged_primary=staged_dataset,
            staged_bundle=staged_bundle,
            destination_primary=output,
            destination_bundle=destination_bundle,
        )
        return destination_bundle
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _publish_staged_pair(
    *,
    staged_primary: Path,
    staged_bundle: Path,
    destination_primary: Path,
    destination_bundle: Path,
) -> None:
    expected_fingerprint = _tree_fingerprint(staged_bundle)
    expected_device, expected_inode = _directory_identity(staged_bundle)
    expected_identity = _PublishedBundleIdentity(
        device=expected_device,
        inode=expected_inode,
        tree_fingerprint=expected_fingerprint,
    )
    published_identity: _PublishedBundleIdentity | None = None
    try:
        _publish_no_replace(staged_bundle, destination_bundle)
        published_identity = expected_identity
        _capture_published_bundle_identity(destination_bundle, expected_identity)
        _publish_no_replace(staged_primary, destination_primary)
    except Exception:
        if published_identity is not None:
            try:
                _rollback_published_bundle(destination_bundle, published_identity)
            except RuntimeError as cleanup_error:
                raise cleanup_error from None
        raise


def _capture_published_bundle_identity(
    destination: Path, expected: _PublishedBundleIdentity
) -> _PublishedBundleIdentity:
    device, inode = _directory_identity(destination)
    if (
        device != expected.device
        or inode != expected.inode
        or _tree_fingerprint(destination) != expected.tree_fingerprint
    ):
        raise RuntimeError("published artifact ownership verification failed")
    return expected


def _rollback_published_bundle(
    destination: Path, owner: _PublishedBundleIdentity
) -> None:
    quarantine = destination.parent / f".{destination.name}.rollback-{uuid.uuid4().hex}"
    try:
        _publish_no_replace(destination, quarantine)
    except FileNotFoundError:
        return
    except Exception:
        raise RuntimeError("artifact rollback cleanup conflict") from None

    if _quarantined_tree_matches(quarantine, owner):
        try:
            shutil.rmtree(quarantine)
        except Exception:
            raise RuntimeError("artifact rollback cleanup conflict") from None
        return

    try:
        _publish_no_replace(quarantine, destination)
    except Exception:
        # Both the current destination and the quarantined tree are evidence.
        # Leave both in place for explicit operator recovery.
        raise RuntimeError("artifact rollback cleanup conflict") from None


def _quarantined_tree_matches(
    quarantine: Path, owner: _PublishedBundleIdentity
) -> bool:
    try:
        device, inode = _directory_identity(quarantine)
        fingerprint = _tree_fingerprint(quarantine)
    except (OSError, RuntimeError):
        return False
    return (
        device == owner.device
        and inode == owner.inode
        and fingerprint == owner.tree_fingerprint
    )


def _directory_identity(path: Path) -> tuple[int, int]:
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode) or _is_reparse_point(metadata):
        raise RuntimeError("artifact tree is not an owned directory")
    return metadata.st_dev, metadata.st_ino


def _tree_fingerprint(root: Path) -> str:
    entries: list[dict[str, str | int]] = []

    def visit(directory: Path) -> None:
        before = directory.stat(follow_symlinks=False)
        if not stat.S_ISDIR(before.st_mode) or _is_reparse_point(before):
            raise RuntimeError("artifact tree contains an unsupported filesystem entry")
        relative_directory = directory.relative_to(root).as_posix()
        entries.append({"kind": "directory", "path": relative_directory})
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            metadata = child.stat(follow_symlinks=False)
            if _is_reparse_point(metadata) or stat.S_ISLNK(metadata.st_mode):
                raise RuntimeError("artifact tree contains an unsupported filesystem entry")
            relative = child.relative_to(root).as_posix()
            if stat.S_ISDIR(metadata.st_mode):
                visit(child)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError("artifact tree contains an unsupported filesystem entry")
            digest = sha256(child.read_bytes()).hexdigest()
            after = child.stat(follow_symlinks=False)
            if _stat_signature(metadata) != _stat_signature(after):
                raise RuntimeError("artifact tree changed while fingerprinting")
            entries.append(
                {
                    "kind": "file",
                    "path": relative,
                    "sha256": digest,
                    "size": metadata.st_size,
                }
            )
        after = directory.stat(follow_symlinks=False)
        if _stat_signature(before) != _stat_signature(after):
            raise RuntimeError("artifact tree changed while fingerprinting")

    visit(root)
    return canonical_sha256(cast(JsonValue, entries))


def _stat_signature(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def dataset_provenance(
    dataset: Path, *, dataset_id: str, payloads: tuple[str, ...]
) -> DatasetProvenance:
    manifest = dataset / "manifest.json"
    return DatasetProvenance(
        dataset_id=dataset_id,
        version="1.0",
        manifest_sha256=sha256(manifest.read_bytes()).hexdigest(),
        payloads=tuple(
            ArtifactRef(
                path=path,
                sha256=sha256((dataset / path).read_bytes()).hexdigest(),
                media_type="application/x-ndjson",
            )
            for path in sorted(payloads)
        ),
    )


def _environment(code: CodeProvenance, runtime: RuntimeProvenance) -> str:
    values = {
        "architecture": runtime.architecture,
        "dependency_lock_sha256": runtime.dependency_lock_sha256,
        "git_commit": code.git_commit,
        "implementation": platform.python_implementation(),
        "os": runtime.os,
        "package": "spanvouch",
        "package_version": "0.2.0",
        "python": runtime.python,
        "repository_identity": code.repository_identity,
    }
    return "".join(f"{key}={value}\n" for key, value in values.items())
