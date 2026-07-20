from __future__ import annotations

import ctypes
import errno
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterable, Mapping
from ctypes import wintypes
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import JsonValue

from spanvouch.contracts.artifacts import ArtifactManifest, CodeProvenance
from spanvouch.contracts.versioning import canonical_bytes, canonical_sha256

_REQUIRED_FILENAMES = frozenset(
    {
        "manifest.json",
        "config.json",
        "metrics.json",
        "structured-events.jsonl",
        "environment.txt",
        "README.md",
    }
)
_ENVIRONMENT_FIELDS = frozenset(
    {
        "architecture",
        "dependency_lock_sha256",
        "git_commit",
        "implementation",
        "os",
        "package",
        "package_version",
        "python",
        "repository_identity",
    }
)
_CONFIG_STRING_FIELDS = frozenset(
    {
        "dataset",
        "mode",
        "policy_version",
        "schema_version",
        "source_dataset",
        "verifier",
    }
)
_CONFIG_FIELDS = _CONFIG_STRING_FIELDS | {"seed", "allow_live_api"}
_ENVIRONMENT_VALUE = re.compile(r"^[A-Za-z0-9._+:/ -]+$")
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?:api[_\s-]?key|access[_\s-]?key|authorization|authentication|"
    r"password|client[_\s-]?secret|session[_\s-]?token|credential)\s*(?:=|:)",
    re.IGNORECASE,
)
_AUTH_SCHEME = re.compile(r"\b(?:basic|bearer|token)\s+\S+", re.IGNORECASE)
_TOKEN_PREFIX = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"glpat-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{16,}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16})\b"
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_PEM_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")
_LEXICAL_ATOM = re.compile(r"[A-Za-z0-9_-]+")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OTEL_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_OTEL_SPAN_ID = re.compile(r"^[0-9a-f]{16}$")
_CORPUS_PAYLOAD_PATH = re.compile(
    r"^(?:parity|records|traces)/sha256/[0-9a-f]{64}\.json$"
)
_CORPUS_PAIR_IDENTITY = re.compile(
    r"^(?:supportlab|opslab):[a-z0-9_-]+:[a-z0-9_-]+:[1-9][0-9]*:-?[0-9]+$"
)
_CORPUS_HASH_FIELDS = frozenset(
    {
        "datasetmanifestsha256",
        "dependencylocksha256",
        "environmentsha256",
        "evidenceselectorsha256",
        "experimentconfigsha256",
        "injectiontriggersha256",
        "parityresultssha256",
        "paritypayloadssha256",
        "payloadssha256",
        "recordsha256",
        "recordssha256",
        "resultsha256",
        "runtimeconfigsha256",
        "scenarioinputsha256",
        "terminalpredicatesha256",
        "tracesha256",
        "tracessha256",
    }
)
_CORPUS_TRACE_ID_PATHS = frozenset(
    {
        ("corpus_record", "trace", "trace_id"),
        ("corpus_record", "trace", "spans", "trace_id"),
        ("corpus_trace", "trace_id"),
        ("corpus_trace", "spans", "trace_id"),
    }
)
_CORPUS_SPAN_ID_PATHS = frozenset(
    {
        ("corpus_record", "trace", "spans", "span_id"),
        ("corpus_record", "trace", "spans", "parent_span_id"),
        ("corpus_trace", "spans", "span_id"),
        ("corpus_trace", "spans", "parent_span_id"),
    }
)
_CORPUS_EXACT_SHA256_PATHS = frozenset(
    {
        ("corpus_parity_results", "mismatches", "reference_sha256"),
        ("corpus_parity_results", "mismatches", "candidate_sha256"),
        ("corpus_parity_results", "result", "mismatches", "reference_sha256"),
        ("corpus_parity_results", "result", "mismatches", "candidate_sha256"),
        (
            "corpus_parity_results",
            "result",
            "framework_incompatibility",
            "error_sha256",
        ),
        ("corpus_record", "failure", "error_sha256"),
    }
)
_CORPUS_SANITIZED_REFUND_PATHS = frozenset(
    {
        ("corpus_record", "trace", "spans", "attributes", "tool.result"),
        ("corpus_trace", "spans", "attributes", "tool.result"),
    }
)
_EVALUATION_IDENTIFIER = re.compile(r"^(?:verifier|finding|gap)-[0-9a-f]{64}$")
_EVALUATION_CANDIDATE = re.compile(r"^[a-z0-9_-]+(?:--[a-z0-9_-]+)?$")
_SANITIZED_REFUND_VALUE = re.compile(
    r"^refund_id='[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}' "
    r"order_id='[a-z0-9-]+' amount=Decimal\('[0-9]+(?:\.[0-9]+)?'\) "
    r"reason='[a-z0-9 -]+' idempotency_key='[a-z0-9_-]+' "
    r"approved_by='[a-z0-9._-]+@example\.test'$"
)
_HASH_FIELDS = frozenset(
    {
        "dependencylocksha256",
        "generationconfigsha256",
        "manifestsha256",
        "policysha256",
        "rulesetversion",
        "promptsha256",
        "reportsha256",
        "valuesha256",
        "tracessha256",
        "labelssha256",
        "candidatessha256",
        "sourcemanifestsha256",
        "sha256",
    }
)
_HASH_PATHS = frozenset(
    {
        ("manifest", "configuration", "sha256"),
        ("manifest", "datasets", "manifest_sha256"),
        ("manifest", "datasets", "payloads", "sha256"),
        ("manifest", "inputs", "sha256"),
        ("manifest", "models", "generation_config_sha256"),
        ("manifest", "models", "prompt_sha256"),
        ("manifest", "outputs", "sha256"),
        ("manifest", "runtime", "dependency_lock_sha256"),
        ("environment", "dependency_lock_sha256"),
        ("metrics", "candidates_sha256"),
        ("metrics", "labels_sha256"),
        ("metrics", "policy_sha256"),
        ("metrics", "source_manifest_sha256"),
        ("metrics", "traces_sha256"),
        ("metrics", "samples", "report", "evidence", "value_sha256"),
        ("metrics", "samples", "report", "provenance", "prompt_sha256"),
        ("metrics", "samples", "report", "provenance", "ruleset_version"),
        ("metrics", "samples", "verifier_report", "report_sha256"),
        ("metrics", "samples", "verifier_report", "provenance", "prompt_sha256"),
        ("metrics", "samples", "semantic_verifier_report", "report_sha256"),
        (
            "metrics",
            "samples",
            "semantic_verifier_report",
            "provenance",
            "prompt_sha256",
        ),
    }
)
_CANDIDATE_ID_PATHS = frozenset(
    {
        ("metrics", "samples", "candidate_id"),
        ("metrics", "samples", "source_run_id"),
        ("metrics", "samples", "run_id"),
        ("metrics", "samples", "report", "run_id"),
    }
)
_EVALUATION_ID_PATHS = frozenset(
    {
        ("metrics", "samples", "verifier_report", "verifier_run_id"),
        ("metrics", "samples", "verifier_report", "findings", "finding_id"),
        ("metrics", "samples", "verifier_report", "evidence_gaps", "gap_id"),
        ("metrics", "samples", "semantic_verifier_report", "verifier_run_id"),
        (
            "metrics",
            "samples",
            "semantic_verifier_report",
            "findings",
            "finding_id",
        ),
        (
            "metrics",
            "samples",
            "semantic_verifier_report",
            "evidence_gaps",
            "gap_id",
        ),
    }
)


def collect_git_provenance(repository: Path) -> CodeProvenance:
    """Collect non-secret Git identity for an artifact manifest."""
    root = _git(repository, "rev-parse", "--show-toplevel")
    commit = _git(repository, "rev-parse", "HEAD")
    dirty = bool(_git(repository, "status", "--porcelain"))
    return CodeProvenance(
        git_commit=commit,
        repository_identity=f"local:{Path(root).name}",
        dirty_worktree=dirty,
    )


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        message = result.stderr.strip() or "Git provenance collection failed"
        raise ValueError(message)
    return result.stdout.strip()


class ArtifactBundleWriter:
    """Write a complete evaluation bundle with atomic no-replace publication."""

    def __init__(self, destination: Path) -> None:
        self._destination = destination

    def write(
        self,
        *,
        manifest: ArtifactManifest,
        config: JsonValue,
        metrics: JsonValue,
        structured_events: Iterable[JsonValue],
        environment: str,
        readme: str,
    ) -> tuple[Path, ...]:
        if self._destination.exists():
            raise FileExistsError(
                f"artifact bundle destination already exists: {self._destination}"
            )
        self._destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(
                dir=self._destination.parent,
                prefix=f".{self._destination.name}.tmp-",
            )
        )
        try:
            contents = self._serialize_contents(
                manifest=manifest,
                config=config,
                metrics=metrics,
                structured_events=structured_events,
                environment=environment,
                readme=readme,
            )
            self._verify_declared_hashes(manifest, contents)
            for filename, content in contents.items():
                target = temporary / filename
                target.write_bytes(content)
                if target.read_bytes() != content:
                    raise ValueError(f"artifact write verification failed: {filename}")
            if {path.name for path in temporary.iterdir()} != _REQUIRED_FILENAMES:
                raise ValueError("artifact bundle must contain exactly the required files")
            _publish_no_replace(temporary, self._destination)
            return tuple(self._destination / filename for filename in sorted(_REQUIRED_FILENAMES))
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise

    def _serialize_contents(
        self,
        *,
        manifest: ArtifactManifest,
        config: JsonValue,
        metrics: JsonValue,
        structured_events: Iterable[JsonValue],
        environment: str,
        readme: str,
    ) -> dict[str, bytes]:
        events = tuple(structured_events)
        _require_safe("manifest", manifest.model_dump(mode="python"))
        _validate_config(config)
        _require_safe("metrics", metrics)
        _require_safe("structured_events", events)
        _validate_environment(environment)
        _require_safe("readme", readme)
        return {
            "manifest.json": canonical_bytes(manifest) + b"\n",
            "config.json": canonical_bytes(config) + b"\n",
            "metrics.json": canonical_bytes(metrics) + b"\n",
            "structured-events.jsonl": b"".join(canonical_bytes(event) + b"\n" for event in events),
            "environment.txt": _normalized_text(environment),
            "README.md": _normalized_text(readme),
        }

    def _verify_declared_hashes(
        self, manifest: ArtifactManifest, contents: Mapping[str, bytes]
    ) -> None:
        bundle_paths = set(contents) - {"manifest.json"}
        output_paths = {reference.path for reference in manifest.outputs}
        declared_paths = {
            manifest.configuration.path,
            *(reference.path for reference in manifest.inputs),
            *output_paths,
        }
        if (
            manifest.configuration.path != "config.json"
            or any(reference.path != "config.json" for reference in manifest.inputs)
            or output_paths != bundle_paths - {"config.json"}
            or declared_paths != bundle_paths
        ):
            raise ValueError("bundle declared refs must cover exactly the generated files")
        references = (manifest.configuration, *manifest.inputs, *manifest.outputs)
        for reference in references:
            content = contents[reference.path]
            if _artifact_digest(reference.path, content) != reference.sha256:
                raise ValueError(f"artifact SHA-256 mismatch: {reference.path}")


def _artifact_digest(path: str, content: bytes) -> str:
    if path.endswith(".json"):
        return canonical_sha256(json.loads(content))
    return sha256(content).hexdigest()


def _normalized_text(value: str) -> bytes:
    return (value.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n").encode("utf-8")


def _publish_no_replace(source: Path, destination: Path, *, platform: str | None = None) -> None:
    """Atomically publish *source* only when *destination* does not exist."""
    current_platform = sys.platform if platform is None else platform
    if current_platform == "win32":
        os.rename(source, destination)
        return
    if current_platform.startswith("linux"):
        _linux_rename_no_replace(source, destination)
        return
    raise RuntimeError("atomic no-replace publication is unsupported on this platform")


def publish_directory_no_replace(source: Path, destination: Path) -> None:
    """Publish an already-built directory atomically without replacing a destination."""
    _publish_no_replace(source, destination)


@dataclass(frozen=True)
class OwnedDirectoryIdentity:
    """No-follow native identity captured for a process-owned staging directory."""

    device: int
    inode: int
    tree_fingerprint: str = ""
    native_entries: Any = ()


@dataclass(frozen=True)
class OwnedDirectoryRootIdentity:
    """No-follow root identity fixed immediately after staging creation."""

    device: int
    inode: int


@dataclass(frozen=True)
class VerifiedDirectorySnapshot:
    """Byte-exact directory contents captured through pinned/no-follow handles."""

    files: Mapping[str, bytes]
    directories: frozenset[str]


def create_owned_staging_directory(
    destination: Path,
) -> tuple[Path, OwnedDirectoryRootIdentity]:
    """Create sibling staging and immediately fix its no-follow root identity."""
    if os.path.lexists(destination):
        raise FileExistsError(f"artifact bundle destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _require_real_directory(destination.parent)
    staging = Path(
        tempfile.mkdtemp(
            dir=destination.parent,
            prefix=(
                f".{destination.name}.tmp-"
                if sys.platform == "win32"
                else f".{destination.name}.rollback-"
            ),
        )
    )
    metadata = staging.stat(follow_symlinks=False)
    return staging, OwnedDirectoryRootIdentity(metadata.st_dev, metadata.st_ino)


def capture_owned_directory_identity(staging: Path) -> OwnedDirectoryIdentity:
    """Capture root and complete-tree identity immediately before publication."""
    from spanvouch.evaluation.provenance import (
        _snapshot_portable_bundle_identity,
        _snapshot_windows_bundle_identity,
    )

    captured = (
        _snapshot_windows_bundle_identity(staging)
        if sys.platform == "win32"
        else _snapshot_portable_bundle_identity(staging)
    )
    return OwnedDirectoryIdentity(
        device=captured.device,
        inode=captured.inode,
        tree_fingerprint=captured.tree_fingerprint,
        native_entries=(
            captured.native_entries
            if sys.platform == "win32"
            else _capture_posix_tree_entries(staging)
        ),
    )


def delete_owned_staging_directory(
    staging: Path, identity: OwnedDirectoryIdentity
) -> bool:
    """Quarantine and delete only a complete tree matching the captured identity."""
    if sys.platform != "win32":
        return _delete_posix_owned_staging(staging, identity)
    from spanvouch.evaluation.provenance import (
        _delete_owned_quarantine,
        _PublishedBundleIdentity,
    )

    owner = _PublishedBundleIdentity(
        device=identity.device,
        inode=identity.inode,
        tree_fingerprint=identity.tree_fingerprint,
        native_entries=identity.native_entries,
    )
    quarantine = staging.parent / f".{staging.name}.rollback-{uuid.uuid4().hex}"
    try:
        _publish_no_replace(staging, quarantine)
    except FileNotFoundError:
        return True
    try:
        try:
            owned = _delete_owned_quarantine(quarantine, owner)
        except RuntimeError:
            # Tree mismatch/reparse is detected before the handle-based delete begins.
            owned = False
        if owned:
            return True
        _publish_no_replace(quarantine, staging)
        return False
    except Exception:
        raise RuntimeError("artifact staging cleanup conflict") from None


def quarantine_owned_staging_directory(
    staging: Path, identity: OwnedDirectoryRootIdentity
) -> bool:
    """Move an identity-matching partial staging root out of the temp namespace.

    The quarantine is retained.  Cleanup errors never replace the operation's
    original exception, and a root already replaced before validation is untouched.
    """
    if not _owned_root_matches(staging, identity):
        return False
    if sys.platform != "win32":
        # POSIX staging already lives in the rollback namespace.  Retain it in
        # place so no pathname operation can relocate an adversarial replacement.
        return False
    quarantine = _posix_cleanup_tombstone(staging)
    try:
        _publish_no_replace(staging, quarantine)
    except (FileNotFoundError, OSError, RuntimeError):
        return False
    try:
        quarantined = quarantine.stat(follow_symlinks=False)
    except (FileNotFoundError, OSError):
        return False
    return (
        stat.S_ISDIR(quarantined.st_mode)
        and not _is_reparse_point(quarantined)
        and (quarantined.st_dev, quarantined.st_ino)
        == (identity.device, identity.inode)
    )


def _owned_root_matches(staging: Path, identity: OwnedDirectoryRootIdentity) -> bool:
    try:
        metadata = staging.stat(follow_symlinks=False)
    except (FileNotFoundError, OSError):
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and not _is_reparse_point(metadata)
        and (metadata.st_dev, metadata.st_ino) == (identity.device, identity.inode)
    )


def read_verified_directory_tree(root: Path) -> VerifiedDirectorySnapshot:
    """Read a tree without following replaceable path components or reparse points."""
    if sys.platform == "win32":
        return _read_verified_windows_tree(root)
    return _read_verified_posix_tree(root)


def _read_verified_windows_tree(root: Path) -> VerifiedDirectorySnapshot:
    from spanvouch.evaluation.provenance import (
        _close_windows_tree,
        _pin_windows_tree,
        _windows_kernel32,
        _windows_tree_fingerprint,
    )

    kernel32 = _windows_kernel32()
    pinned = _pin_windows_tree(root, kernel32)
    try:
        try:
            _windows_tree_fingerprint(pinned)
        except RuntimeError as error:
            raise ValueError("artifact tree contains symlink or reparse point") from error
        files: dict[str, bytes] = {}
        directories: set[str] = set()

        def collect(node: Any) -> None:
            if node.relative_path not in {"", "."} and node.is_directory:
                directories.add(node.relative_path)
            if node.is_directory:
                for child in node.children:
                    collect(child)
                return
            content = _read_windows_node_bytes(node, kernel32)
            if node.content_sha256 != sha256(content).hexdigest():
                raise RuntimeError("artifact tree changed while reading")
            files[node.relative_path] = content

        collect(pinned)
        return VerifiedDirectorySnapshot(files=files, directories=frozenset(directories))
    finally:
        _close_windows_tree(pinned, kernel32)


def _read_windows_node_bytes(node: Any, kernel32: Any) -> bytes:
    kernel32.SetFilePointerEx.argtypes = (
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    )
    kernel32.SetFilePointerEx.restype = wintypes.BOOL
    if not kernel32.SetFilePointerEx(
        wintypes.HANDLE(node.handle),
        0,
        None,
        0,
    ):
        raise OSError(ctypes.get_last_error(), "unable to rewind artifact payload")
    chunks: list[bytes] = []
    buffer = ctypes.create_string_buffer(64 * 1024)
    while True:
        read = wintypes.DWORD()
        if not kernel32.ReadFile(
            wintypes.HANDLE(node.handle),
            buffer,
            len(buffer),
            ctypes.byref(read),
            None,
        ):
            raise OSError(ctypes.get_last_error(), "unable to read artifact payload")
        if read.value == 0:
            return b"".join(chunks)
        chunks.append(buffer.raw[: read.value])


def _read_verified_posix_tree(  # pragma: no cover - POSIX dir_fd API is absent on Windows
    root: Path,
) -> VerifiedDirectorySnapshot:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    root_fd = os.open(root, os.O_RDONLY | no_follow | directory_flag)
    files: dict[str, bytes] = {}
    directories: set[str] = set()

    def same_identity(left: os.stat_result, right: os.stat_result) -> bool:
        return (left.st_dev, left.st_ino, left.st_mode) == (
            right.st_dev,
            right.st_ino,
            right.st_mode,
        )

    def read_file(directory_fd: int, name: str, expected: os.stat_result) -> bytes:
        descriptor = os.open(name, os.O_RDONLY | no_follow, dir_fd=directory_fd)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or not same_identity(expected, before):
                raise RuntimeError("artifact tree changed while reading")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 64 * 1024):
                chunks.append(chunk)
            after = os.fstat(descriptor)
            if _artifact_stat_signature(before) != _artifact_stat_signature(after):
                raise RuntimeError("artifact tree changed while reading")
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def visit(directory_fd: int, relative: str) -> None:
        before = os.fstat(directory_fd)
        if not stat.S_ISDIR(before.st_mode):
            raise ValueError("artifact tree root is not a directory")
        for name in sorted(os.listdir(directory_fd)):
            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            child_relative = f"{relative}/{name}" if relative else name
            if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(metadata):
                raise ValueError("artifact tree contains symlink or reparse point")
            if stat.S_ISDIR(metadata.st_mode):
                child_fd = os.open(
                    name,
                    os.O_RDONLY | no_follow | directory_flag,
                    dir_fd=directory_fd,
                )
                try:
                    if not same_identity(metadata, os.fstat(child_fd)):
                        raise RuntimeError("artifact tree changed while reading")
                    directories.add(child_relative)
                    visit(child_fd, child_relative)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("artifact tree contains unsupported filesystem node")
            files[child_relative] = read_file(directory_fd, name, metadata)
        if _artifact_stat_signature(before) != _artifact_stat_signature(
            os.fstat(directory_fd)
        ):
            raise RuntimeError("artifact tree changed while reading")

    try:
        visit(root_fd, "")
        return VerifiedDirectorySnapshot(files=files, directories=frozenset(directories))
    finally:
        os.close(root_fd)


def _artifact_stat_signature(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _capture_posix_tree_entries(  # pragma: no cover - POSIX dir_fd API is absent on Windows
    root: Path,
) -> tuple[tuple[str, int, int, int, int, int, str | None], ...]:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    root_fd = os.open(root, os.O_RDONLY | no_follow | directory_flag)
    entries: list[tuple[str, int, int, int, int, int, str | None]] = []

    def signature(
        relative: str, metadata: os.stat_result, content_sha256: str | None
    ) -> tuple[str, int, int, int, int, int, str | None]:
        return (
            relative,
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
            content_sha256,
        )

    def visit(directory_fd: int, relative: str) -> None:
        entries.append(signature(relative, os.fstat(directory_fd), None))
        for name in sorted(os.listdir(directory_fd)):
            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            child_relative = f"{relative}/{name}" if relative else name
            if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(metadata):
                raise RuntimeError("artifact tree contains a symlink or reparse point")
            if stat.S_ISDIR(metadata.st_mode):
                child_fd = os.open(
                    name,
                    os.O_RDONLY | no_follow | directory_flag,
                    dir_fd=directory_fd,
                )
                try:
                    if _artifact_stat_signature(metadata) != _artifact_stat_signature(
                        os.fstat(child_fd)
                    ):
                        raise RuntimeError("artifact tree changed while capturing identity")
                    visit(child_fd, child_relative)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError("artifact tree contains an unsupported node")
            file_fd = os.open(name, os.O_RDONLY | no_follow, dir_fd=directory_fd)
            try:
                before = os.fstat(file_fd)
                if _artifact_stat_signature(metadata) != _artifact_stat_signature(before):
                    raise RuntimeError("artifact tree changed while capturing identity")
                digest = sha256()
                while chunk := os.read(file_fd, 64 * 1024):
                    digest.update(chunk)
                after = os.fstat(file_fd)
                if _artifact_stat_signature(before) != _artifact_stat_signature(after):
                    raise RuntimeError("artifact tree changed while capturing identity")
                entries.append(signature(child_relative, before, digest.hexdigest()))
            finally:
                os.close(file_fd)

    try:
        visit(root_fd, "")
        return tuple(entries)
    finally:
        os.close(root_fd)


def _delete_posix_owned_staging(  # pragma: no cover - POSIX dir_fd API is absent on Windows
    staging: Path, identity: OwnedDirectoryIdentity
) -> bool:
    """Validate a POSIX rollback tree, but retain it at its existing name.

    POSIX does not expose an inode-conditional unlink/rmdir operation.  Even after
    validating a pinned descriptor, a concurrent rename can replace its pathname
    before removal.  Keep the uniquely named staging tree as a fail-closed tombstone;
    a separate trusted maintenance process may remove it while the corpus is idle.
    """
    try:
        actual_entries = _capture_posix_tree_entries(staging)
    except (OSError, RuntimeError):
        return False
    expected_entries: tuple[
        tuple[str, int, int, int, int, int, str | None], ...
    ] = identity.native_entries
    if actual_entries != expected_entries:
        return False
    # The tree already has a rollback name.  Even a confirmed owner can be
    # replaced after the final handle closes, so retain it in place.
    return False


def _posix_cleanup_tombstone(staging: Path) -> Path:
    destination_name = staging.name.removeprefix(".").partition(".tmp-")[0]
    return staging.parent / f".{destination_name}.rollback-{uuid.uuid4().hex}"


def _require_real_directory(path: Path) -> None:
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode) or _is_reparse_point(metadata):
        raise ValueError("artifact path must be a real directory")


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _linux_rename_no_replace(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as error:
        raise RuntimeError(
            "atomic no-replace publication is unsupported on this platform"
        ) from error
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        _AT_FDCWD,
        os.fsencode(source),
        _AT_FDCWD,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(f"artifact bundle destination already exists: {destination}")
    raise OSError(error_number, os.strerror(error_number), destination)


def _unsafe_artifact_content() -> None:
    raise ValueError("unsafe artifact content")


def _validate_config(value: Any) -> None:
    _require_safe("config", value)
    if not isinstance(value, Mapping):
        _unsafe_artifact_content()
    for key, item in value.items():
        if not isinstance(key, str) or key not in _CONFIG_FIELDS:
            _unsafe_artifact_content()
        if key in _CONFIG_STRING_FIELDS:
            if not isinstance(item, str) or not item:
                _unsafe_artifact_content()
        elif key == "seed":
            if not isinstance(item, int) or isinstance(item, bool):
                _unsafe_artifact_content()
        elif not isinstance(item, bool):
            _unsafe_artifact_content()


def _validate_environment(value: str) -> None:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    if not normalized:
        _unsafe_artifact_content()
    for line in normalized.split("\n"):
        key, separator, item = line.partition("=")
        if (
            separator != "="
            or key not in _ENVIRONMENT_FIELDS
            or not item
            or not _ENVIRONMENT_VALUE.fullmatch(item)
        ):
            _unsafe_artifact_content()
        _require_safe("environment", {key: item})


class ArtifactSecretClassifier:
    """Fail-closed recursive classifier for values safe to persist in artifacts."""

    def require_safe(self, value: Any, *, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, Mapping):
            for child_key, item in value.items():
                if not isinstance(child_key, str) or (
                    self._is_sensitive_key(child_key)
                    and not self._is_explicit_safe_key(child_key, path)
                ):
                    _unsafe_artifact_content()
                self.require_safe(item, path=(*path, child_key))
            return
        if isinstance(value, (tuple, list)):
            for item in value:
                self.require_safe(item, path=path)
            return
        if isinstance(value, str) and self._is_sensitive_string(value, path=path):
            _unsafe_artifact_content()

    def _is_sensitive_key(self, key: str) -> bool:
        tokens = self._key_tokens(key)
        normalized = "".join(tokens)
        if normalized in _HASH_FIELDS or normalized in {
            "gitcommit",
            "inputtokens",
            "outputtokens",
            "totaltokens",
        }:
            return False
        sensitive_concepts = (
            "apikey",
            "accesskey",
            "privatekey",
            "rawresponse",
            "hiddenreasoning",
            "chainofthought",
        )
        if any(concept in normalized for concept in sensitive_concepts):
            return True
        if any(
            token
            in {
                "key",
                "secret",
                "credential",
                "password",
                "passwd",
                "authentication",
                "authorization",
                "token",
                "prompt",
                "reasoning",
                "header",
                "headers",
                "raw",
                "response",
            }
            for token in tokens
        ):
            return True
        return any(
            pair in tuple(zip(tokens, tokens[1:], strict=False))
            for pair in (("provider", "body"), ("raw", "body"), ("response", "body"))
        )

    @staticmethod
    def _is_explicit_safe_key(key: str, path: tuple[str, ...]) -> bool:
        """Permit only verifier provenance metadata in the metrics payload."""
        return (
            key in {"prompt_version", "prompt_sha256"}
            and path
            in {
                ("metrics", "samples", "verifier_report", "provenance"),
                ("metrics", "samples", "semantic_verifier_report", "provenance"),
                ("metrics", "samples", "report", "provenance"),
            }
        )

    def _is_sensitive_string(self, value: str, *, path: tuple[str, ...]) -> bool:
        """Run non-bypassable credential and opaque-atom scans before field shapes."""
        if self._has_credential_signature(value):
            return True
        if self._is_cryptographic_bypass(value, path=path):
            return False
        if path in _CORPUS_SANITIZED_REFUND_PATHS and _SANITIZED_REFUND_VALUE.fullmatch(
            value
        ):
            return False
        if path == (
            "metrics",
            "samples",
            "report",
            "evidence",
            "observed_value",
        ) and _SANITIZED_REFUND_VALUE.fullmatch(value):
            return False
        return any(self._is_high_entropy(atom) for atom in _LEXICAL_ATOM.findall(value))

    @staticmethod
    def _has_credential_signature(value: str) -> bool:
        try:
            parsed = urlsplit(value)
        except ValueError:
            parsed = None
        if parsed is not None and (parsed.username is not None or parsed.password is not None):
            return True
        return bool(
            _CREDENTIAL_ASSIGNMENT.search(value)
            or _AUTH_SCHEME.search(value)
            or _TOKEN_PREFIX.search(value)
            or _JWT.search(value)
            or _PEM_PRIVATE_KEY.search(value)
        )

    @staticmethod
    def _key_tokens(key: str) -> tuple[str, ...]:
        camel_spaced = _CAMEL_BOUNDARY.sub(" ", key)
        return tuple(
            token for token in re.split(r"[^A-Za-z0-9]+", camel_spaced.casefold()) if token
        )

    @staticmethod
    def _is_cryptographic_bypass(value: str, *, path: tuple[str, ...]) -> bool:
        if not path:
            return False
        if path in _CORPUS_TRACE_ID_PATHS and _OTEL_TRACE_ID.fullmatch(value):
            return True
        if path in _CORPUS_SPAN_ID_PATHS and _OTEL_SPAN_ID.fullmatch(value):
            return True
        if path in _CORPUS_EXACT_SHA256_PATHS and _SHA256.fullmatch(value):
            return True
        if (
            path[0] in {"corpus_manifest", "corpus_parity_results"}
            and path[-1] == "pair_identity"
            and _CORPUS_PAIR_IDENTITY.fullmatch(value)
        ):
            return True
        field = path[-1]
        normalized = "".join(ArtifactSecretClassifier._key_tokens(field))
        if path in _HASH_PATHS and normalized in _HASH_FIELDS and _SHA256.fullmatch(value):
            return True
        if (
            path[0]
            in {
                "corpus_manifest",
                "corpus_parity_results",
                "corpus_record",
                "corpus_trace",
            }
            and normalized in _CORPUS_HASH_FIELDS
            and _SHA256.fullmatch(value)
        ):
            return True
        if (
            path[0] == "corpus_manifest"
            and path[-1] in {"record_path", "result_path", "trace_path"}
            and _CORPUS_PAYLOAD_PATH.fullmatch(value)
        ):
            return True
        if (
            path in {
                ("manifest", "code", "git_commit"),
                ("environment", "git_commit"),
                ("metrics", "git_commit"),
            }
            and _GIT_COMMIT.fullmatch(value) is not None
        ):
            return True
        if (
            path[0] in {"corpus_manifest", "corpus_record"}
            and normalized == "gitcommit"
            and _GIT_COMMIT.fullmatch(value) is not None
        ):
            return True
        if path in _EVALUATION_ID_PATHS:
            return _EVALUATION_IDENTIFIER.fullmatch(value) is not None
        if path in {
            ("metrics", "verifier_version"),
            ("metrics", "samples", "verifier_report", "provenance", "verifier_version"),
            (
                "metrics",
                "samples",
                "semantic_verifier_report",
                "provenance",
                "verifier_version",
            ),
        }:
            return _SHA256.fullmatch(value) is not None
        return path in _CANDIDATE_ID_PATHS and (
            _EVALUATION_CANDIDATE.fullmatch(value) is not None
        )

    @staticmethod
    def _is_high_entropy(candidate: str) -> bool:
        if len(candidate) < 32 or len(set(candidate)) < 10:
            return False
        entropy = -sum(
            (count / len(candidate)) * math.log2(count / len(candidate))
            for count in (candidate.count(character) for character in set(candidate))
        )
        return entropy >= 3.3


_ARTIFACT_SECRET_CLASSIFIER = ArtifactSecretClassifier()


def _require_safe(location: str, value: Any) -> None:
    _ARTIFACT_SECRET_CLASSIFIER.require_safe(value, path=(location,))


def require_safe_artifact_content(location: str, value: Any) -> None:
    """Apply the fail-closed artifact classifier at a named persistence boundary."""
    _require_safe(location, value)
