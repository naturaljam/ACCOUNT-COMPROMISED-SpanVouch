"""Deterministic, injectable provenance and artifact bundle construction."""

from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import stat
import sys
import tempfile
import uuid
from collections.abc import Callable, Mapping
from ctypes import wintypes
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Protocol, cast

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


@dataclass
class _WindowsPinnedNode:
    handle: int
    path: Path
    relative_path: str
    device: int
    inode: int
    is_directory: bool
    is_reparse: bool
    size: int
    content_sha256: str | None
    children: list[_WindowsPinnedNode]


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("file_attributes", wintypes.DWORD),
        ("creation_time", wintypes.FILETIME),
        ("last_access_time", wintypes.FILETIME),
        ("last_write_time", wintypes.FILETIME),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    ]


class _FileDispositionInformation(ctypes.Structure):
    _fields_ = [("delete_file", wintypes.BOOL)]


_GENERIC_READ = 0x80000000
_DELETE = 0x00010000
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_DISPOSITION_INFO_CLASS = 4
_O_DIRECTORY = cast(int, vars(os).get("O_DIRECTORY", 0))
_O_NOFOLLOW = cast(int, vars(os).get("O_NOFOLLOW", 0))


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

    try:
        owned = _delete_owned_quarantine(quarantine, owner)
    except Exception:
        raise RuntimeError("artifact rollback cleanup conflict") from None
    if owned:
        return

    try:
        _publish_no_replace(quarantine, destination)
    except Exception:
        # Both the current destination and the quarantined tree are evidence.
        # Leave both in place for explicit operator recovery.
        raise RuntimeError("artifact rollback cleanup conflict") from None


def _delete_owned_quarantine(
    quarantine: Path, owner: _PublishedBundleIdentity
) -> bool:
    if sys.platform == "win32":
        return _delete_windows_owned_quarantine(quarantine, owner)
    return _delete_posix_owned_quarantine(quarantine, owner)


def _delete_windows_owned_quarantine(
    quarantine: Path, owner: _PublishedBundleIdentity
) -> bool:
    kernel32 = _windows_kernel32()
    root = _pin_windows_tree(quarantine, kernel32)
    try:
        if root.is_reparse:
            return False
        if (
            root.device != owner.device
            or root.inode != owner.inode
            or _windows_tree_fingerprint(root) != owner.tree_fingerprint
        ):
            return False
        _dispose_windows_tree(root, kernel32)
        return True
    finally:
        _close_windows_tree(root, kernel32)


def _windows_kernel32() -> Any:
    loader = cast(Any, vars(ctypes)["WinDLL"])
    kernel32 = loader("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    )
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.SetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _pin_windows_tree(path: Path, kernel32: Any) -> _WindowsPinnedNode:
    root = path

    def pin(current: Path) -> _WindowsPinnedNode:
        raw_handle = kernel32.CreateFileW(
            str(current),
            _GENERIC_READ | _DELETE | _FILE_READ_ATTRIBUTES,
            _FILE_SHARE_READ,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if raw_handle == invalid_handle:
            raise OSError(ctypes.get_last_error(), "unable to pin rollback artifact")
        handle = int(raw_handle)
        node: _WindowsPinnedNode | None = None
        try:
            information = _ByHandleFileInformation()
            if not kernel32.GetFileInformationByHandle(
                wintypes.HANDLE(handle), ctypes.byref(information)
            ):
                raise OSError(ctypes.get_last_error(), "unable to inspect rollback artifact")
            metadata = current.stat(follow_symlinks=False)
            is_directory = bool(information.file_attributes & _FILE_ATTRIBUTE_DIRECTORY)
            is_reparse = bool(
                information.file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT
            )
            relative = current.relative_to(root).as_posix()
            content_sha256: str | None = None
            size = (information.file_size_high << 32) | information.file_size_low
            children: list[_WindowsPinnedNode] = []
            node = _WindowsPinnedNode(
                handle=handle,
                path=current,
                relative_path=relative,
                device=metadata.st_dev,
                inode=metadata.st_ino,
                is_directory=is_directory,
                is_reparse=is_reparse,
                size=size,
                content_sha256=None,
                children=children,
            )
            if is_reparse:
                return node
            if is_directory:
                for child in sorted(current.iterdir(), key=lambda item: item.name):
                    children.append(pin(child))
            else:
                content_sha256 = _hash_windows_handle(handle, kernel32)
                node.content_sha256 = content_sha256
            return node
        except Exception:
            if node is not None:
                _close_windows_tree(node, kernel32)
            else:
                kernel32.CloseHandle(wintypes.HANDLE(handle))
            raise

    return pin(path)


def _hash_windows_handle(handle: int, kernel32: Any) -> str:
    digest = sha256()
    buffer = ctypes.create_string_buffer(64 * 1024)
    while True:
        read = wintypes.DWORD()
        if not kernel32.ReadFile(
            wintypes.HANDLE(handle),
            buffer,
            len(buffer),
            ctypes.byref(read),
            None,
        ):
            raise OSError(ctypes.get_last_error(), "unable to read rollback artifact")
        if read.value == 0:
            return digest.hexdigest()
        digest.update(buffer.raw[: read.value])


def _windows_tree_fingerprint(root: _WindowsPinnedNode) -> str:
    entries: list[dict[str, str | int]] = []

    def append(node: _WindowsPinnedNode) -> None:
        if node.is_reparse:
            raise RuntimeError("rollback artifact contains a reparse point")
        if node.is_directory:
            entries.append({"kind": "directory", "path": node.relative_path})
            for child in node.children:
                append(child)
            return
        if node.content_sha256 is None:
            raise RuntimeError("rollback artifact fingerprint is incomplete")
        entries.append(
            {
                "kind": "file",
                "path": node.relative_path,
                "sha256": node.content_sha256,
                "size": node.size,
            }
        )

    append(root)
    return canonical_sha256(cast(JsonValue, entries))


def _dispose_windows_tree(node: _WindowsPinnedNode, kernel32: Any) -> None:
    for child in node.children:
        _dispose_windows_tree(child, kernel32)
    disposition = _FileDispositionInformation(delete_file=True)
    if not kernel32.SetFileInformationByHandle(
        wintypes.HANDLE(node.handle),
        _FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        raise OSError(ctypes.get_last_error(), "unable to delete rollback artifact")
    if not kernel32.CloseHandle(wintypes.HANDLE(node.handle)):
        raise OSError(ctypes.get_last_error(), "unable to close rollback artifact")
    node.handle = 0


def _close_windows_tree(node: _WindowsPinnedNode, kernel32: Any) -> None:
    for child in node.children:
        _close_windows_tree(child, kernel32)
    if node.handle:
        kernel32.CloseHandle(wintypes.HANDLE(node.handle))
        node.handle = 0


def _delete_posix_owned_quarantine(
    quarantine: Path, owner: _PublishedBundleIdentity
) -> bool:  # pragma: no cover - exercised on POSIX runners
    if not _O_DIRECTORY or not _O_NOFOLLOW:
        raise RuntimeError("stable descriptor deletion is unsupported")
    root_fd = os.open(
        quarantine,
        os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW,
    )
    try:
        root_metadata = os.fstat(root_fd)
        if (
            root_metadata.st_dev != owner.device
            or root_metadata.st_ino != owner.inode
            or _posix_tree_fingerprint(root_fd) != owner.tree_fingerprint
        ):
            return False
        _delete_posix_children(root_fd)
        current = quarantine.stat(follow_symlinks=False)
        if (
            current.st_dev != root_metadata.st_dev
            or current.st_ino != root_metadata.st_ino
            or not stat.S_ISDIR(current.st_mode)
        ):
            raise RuntimeError("rollback quarantine identity changed")
        os.rmdir(quarantine)
        return True
    finally:
        os.close(root_fd)


def _posix_tree_fingerprint(
    root_fd: int,
) -> str:  # pragma: no cover - exercised on POSIX runners
    entries: list[dict[str, str | int]] = []

    def visit(directory_fd: int, relative: str) -> None:
        entries.append({"kind": "directory", "path": relative})
        for name in sorted(os.listdir(directory_fd)):
            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            child_relative = name if relative == "." else f"{relative}/{name}"
            if stat.S_ISLNK(metadata.st_mode):
                raise RuntimeError("rollback artifact contains a symlink")
            if stat.S_ISDIR(metadata.st_mode):
                child_fd = os.open(
                    name,
                    os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
                try:
                    visit(child_fd, child_relative)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError("rollback artifact contains a special file")
            child_fd = os.open(
                name,
                os.O_RDONLY | _O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            try:
                pinned = os.fstat(child_fd)
                digest = sha256()
                while data := os.read(child_fd, 64 * 1024):
                    digest.update(data)
            finally:
                os.close(child_fd)
            if (metadata.st_dev, metadata.st_ino) != (pinned.st_dev, pinned.st_ino):
                raise RuntimeError("rollback artifact identity changed")
            entries.append(
                {
                    "kind": "file",
                    "path": child_relative,
                    "sha256": digest.hexdigest(),
                    "size": pinned.st_size,
                }
            )

    visit(root_fd, ".")
    return canonical_sha256(cast(JsonValue, entries))


def _delete_posix_children(
    directory_fd: int,
) -> None:  # pragma: no cover - exercised on POSIX runners
    for name in sorted(os.listdir(directory_fd)):
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError("rollback artifact contains a symlink")
        if stat.S_ISDIR(metadata.st_mode):
            child_fd = os.open(
                name,
                os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            try:
                pinned = os.fstat(child_fd)
                _delete_posix_children(child_fd)
                current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
                    raise RuntimeError("rollback artifact identity changed")
                os.rmdir(name, dir_fd=directory_fd)
            finally:
                os.close(child_fd)
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("rollback artifact contains a special file")
        child_fd = os.open(
            name,
            os.O_RDONLY | _O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        try:
            pinned = os.fstat(child_fd)
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
                raise RuntimeError("rollback artifact identity changed")
            os.unlink(name, dir_fd=directory_fd)
        finally:
            os.close(child_fd)


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
