from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import JsonValue

from spanvouch import __version__
from spanvouch.audit.chain import AuditChain, AuditCheckpoint, AuditEvent
from spanvouch.contracts.artifacts import (
    ArtifactManifest,
    ArtifactRef,
    CodeProvenance,
    PackageProvenance,
    RuntimeProvenance,
)
from spanvouch.contracts.versioning import canonical_bytes, canonical_json
from spanvouch.evaluation.artifacts import collect_git_provenance

_REQUIRED_EXPORT_FILES = frozenset(
    {"manifest.json", "events.jsonl", "checkpoints.json", "public-key.pem", "README.md"}
)
_AUDIT_EXPORT_SCHEMA = "spanvouch.audit-export"
_AUDIT_EXPORT_VERSION = "1.0"


@dataclass(frozen=True)
class VerifiedAuditExport:
    project_id: str
    first_event_sequence: int
    last_event_sequence: int
    terminal_event_sha256: str
    manifest_sha256: str
    event_count: int
    checkpoint_count: int
    signing_key_fingerprint: str
    manifest: ArtifactManifest
    events: tuple[AuditEvent, ...]
    checkpoints: tuple[AuditCheckpoint, ...]


def create_audit_export(
    project_id: str,
    output_dir: Path,
    *,
    events: Sequence[AuditEvent],
    checkpoints: Sequence[AuditCheckpoint],
    signing_key_path: Path,
) -> Path:
    if not project_id:
        raise ValueError("project_id is required")
    event_tuple = tuple(events)
    if not event_tuple:
        raise ValueError("audit export requires at least one event")
    AuditChain().verify(event_tuple)
    if any(event.project_id != project_id for event in event_tuple):
        raise ValueError("audit export events belong to a different project")
    signing_key = _load_signing_key(signing_key_path)
    public_key_pem = _public_key_pem(signing_key)
    readme = _readme(project_id, event_tuple[0].event_sequence, event_tuple[-1].event_sequence)
    readme_bytes = _text(readme)
    events_bytes = _events_jsonl(event_tuple)
    descriptor_sha256 = _descriptor_sha256(
        project_id=project_id,
        first_event_sequence=event_tuple[0].event_sequence,
        last_event_sequence=event_tuple[-1].event_sequence,
        terminal_event_sha256=event_tuple[-1].event_sha256,
        events_sha256=_raw_sha256(events_bytes),
        public_key_sha256=_raw_sha256(public_key_pem),
        readme_sha256=_raw_sha256(readme_bytes),
    )
    checkpoint = AuditChain(signing_key=signing_key).create_checkpoint(
        project_id=project_id,
        terminal_event=event_tuple[-1],
        manifest_sha256=descriptor_sha256,
    )
    checkpoint_tuple = (*tuple(checkpoints), checkpoint)
    checkpoints_bytes = _checkpoints_json(checkpoint_tuple)
    manifest = _manifest(
        project_id=project_id,
        first_event_sequence=event_tuple[0].event_sequence,
        last_event_sequence=event_tuple[-1].event_sequence,
        created_at=event_tuple[-1].occurred_at,
        readme_sha256=_raw_sha256(readme_bytes),
        events_sha256=_raw_sha256(events_bytes),
        checkpoints_sha256=_raw_sha256(checkpoints_bytes),
        public_key_sha256=_raw_sha256(public_key_pem),
    )
    manifest_bytes = canonical_bytes(manifest) + b"\n"
    contents = {
        "README.md": readme_bytes,
        "events.jsonl": events_bytes,
        "checkpoints.json": checkpoints_bytes,
        "public-key.pem": public_key_pem,
        "manifest.json": manifest_bytes,
    }
    _write_bundle_no_replace(output_dir, contents)
    return output_dir


def verify_audit_export(bundle_dir: Path) -> VerifiedAuditExport:
    if not bundle_dir.is_dir():
        raise ValueError("audit export bundle must be a directory")
    actual_files = {path.name for path in bundle_dir.iterdir() if path.is_file()}
    if actual_files != _REQUIRED_EXPORT_FILES:
        raise ValueError("audit export bundle has unexpected files")
    manifest = ArtifactManifest.model_validate_json(
        (bundle_dir / "manifest.json").read_text(encoding="utf-8")
    )
    _verify_manifest_file_hashes(bundle_dir, manifest)
    events = _read_events(bundle_dir / "events.jsonl")
    AuditChain().verify(events)
    checkpoints = _read_checkpoints(bundle_dir / "checkpoints.json")
    if not checkpoints:
        raise ValueError("audit export requires at least one checkpoint")
    terminal_event = events[-1]
    public_key_pem = (bundle_dir / "public-key.pem").read_bytes()
    descriptor_sha256 = _descriptor_sha256(
        project_id=terminal_event.project_id,
        first_event_sequence=events[0].event_sequence,
        last_event_sequence=terminal_event.event_sequence,
        terminal_event_sha256=terminal_event.event_sha256,
        events_sha256=_raw_sha256((bundle_dir / "events.jsonl").read_bytes()),
        public_key_sha256=_raw_sha256(public_key_pem),
        readme_sha256=_raw_sha256((bundle_dir / "README.md").read_bytes()),
    )
    public_key = _load_public_key(public_key_pem)
    for checkpoint in checkpoints:
        _verify_checkpoint(
            checkpoint,
            public_key=public_key,
            public_key_pem=public_key_pem,
            expected_project_id=terminal_event.project_id,
            expected_terminal_event=terminal_event,
            expected_manifest_sha256=descriptor_sha256,
        )
    return VerifiedAuditExport(
        project_id=terminal_event.project_id,
        first_event_sequence=events[0].event_sequence,
        last_event_sequence=terminal_event.event_sequence,
        terminal_event_sha256=terminal_event.event_sha256,
        manifest_sha256=descriptor_sha256,
        event_count=len(events),
        checkpoint_count=len(checkpoints),
        signing_key_fingerprint=_raw_sha256(public_key_pem),
        manifest=manifest,
        events=events,
        checkpoints=checkpoints,
    )


def _load_signing_key(path: Path) -> Ed25519PrivateKey:
    private_key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("audit signing key must be an Ed25519 private key")
    return private_key


def _load_public_key(public_key_pem: bytes) -> Ed25519PublicKey:
    public_key = serialization.load_pem_public_key(public_key_pem)
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError("audit public key must be an Ed25519 public key")
    return public_key


def _public_key_pem(signing_key: Ed25519PrivateKey) -> bytes:
    return signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _events_jsonl(events: Sequence[AuditEvent]) -> bytes:
    return b"".join(canonical_bytes(event) + b"\n" for event in events)


def _read_events(path: Path) -> tuple[AuditEvent, ...]:
    events: list[AuditEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(AuditEvent.model_validate(json.loads(line)))
    if not events:
        raise ValueError("audit export requires at least one event")
    return tuple(events)


def _checkpoints_json(checkpoints: Sequence[AuditCheckpoint]) -> bytes:
    payload = [_checkpoint_json(checkpoint) for checkpoint in checkpoints]
    return canonical_bytes(cast(JsonValue, payload)) + b"\n"


def _read_checkpoints(path: Path) -> tuple[AuditCheckpoint, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("audit checkpoints must be a JSON array")
    return tuple(_checkpoint_from_json(item) for item in payload)


def _checkpoint_json(checkpoint: AuditCheckpoint) -> dict[str, JsonValue]:
    return {
        "checkpoint_id": checkpoint.checkpoint_id,
        "project_id": checkpoint.project_id,
        "first_event_sequence": checkpoint.first_event_sequence,
        "last_event_sequence": checkpoint.last_event_sequence,
        "terminal_event_sha256": checkpoint.terminal_event_sha256,
        "manifest_sha256": checkpoint.manifest_sha256,
        "public_key_pem_b64": base64.b64encode(checkpoint.public_key_pem).decode("ascii"),
        "signature_b64": base64.b64encode(checkpoint.signature).decode("ascii"),
        "created_at": checkpoint.created_at.astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def _checkpoint_from_json(value: object) -> AuditCheckpoint:
    if not isinstance(value, dict):
        raise ValueError("audit checkpoint entry must be an object")
    try:
        return AuditCheckpoint(
            checkpoint_id=str(value["checkpoint_id"]),
            project_id=str(value["project_id"]),
            first_event_sequence=int(value["first_event_sequence"]),
            last_event_sequence=int(value["last_event_sequence"]),
            terminal_event_sha256=str(value["terminal_event_sha256"]),
            manifest_sha256=str(value["manifest_sha256"]),
            public_key_pem=base64.b64decode(str(value["public_key_pem_b64"])),
            signature=base64.b64decode(str(value["signature_b64"])),
            created_at=_parse_timestamp(str(value["created_at"])),
        )
    except TypeError:
        raise ValueError("stored audit checkpoint is invalid") from None
    except KeyError:
        raise ValueError("stored audit checkpoint is invalid") from None


def _verify_checkpoint(
    checkpoint: AuditCheckpoint,
    *,
    public_key: Ed25519PublicKey,
    public_key_pem: bytes,
    expected_project_id: str,
    expected_terminal_event: AuditEvent,
    expected_manifest_sha256: str,
) -> None:
    if checkpoint.project_id != expected_project_id:
        raise ValueError("audit checkpoint project mismatch")
    if checkpoint.last_event_sequence != expected_terminal_event.event_sequence:
        raise ValueError("audit checkpoint sequence mismatch")
    if checkpoint.terminal_event_sha256 != expected_terminal_event.event_sha256:
        raise ValueError("audit checkpoint terminal hash mismatch")
    if checkpoint.manifest_sha256 != expected_manifest_sha256:
        raise ValueError("audit checkpoint manifest hash mismatch")
    if checkpoint.public_key_pem != public_key_pem:
        raise ValueError("audit checkpoint public key mismatch")
    payload = {
        "project_id": checkpoint.project_id,
        "first_event_sequence": checkpoint.first_event_sequence,
        "last_event_sequence": checkpoint.last_event_sequence,
        "terminal_event_sha256": checkpoint.terminal_event_sha256,
        "manifest_sha256": checkpoint.manifest_sha256,
    }
    try:
        public_key.verify(
            checkpoint.signature,
            canonical_json(cast(JsonValue, payload)).encode("utf-8"),
        )
    except InvalidSignature:
        raise ValueError("audit checkpoint signature is invalid") from None


def _descriptor_sha256(
    *,
    project_id: str,
    first_event_sequence: int,
    last_event_sequence: int,
    terminal_event_sha256: str,
    events_sha256: str,
    public_key_sha256: str,
    readme_sha256: str,
) -> str:
    descriptor = {
        "schema_name": _AUDIT_EXPORT_SCHEMA,
        "schema_version": _AUDIT_EXPORT_VERSION,
        "project_id": project_id,
        "first_event_sequence": first_event_sequence,
        "last_event_sequence": last_event_sequence,
        "terminal_event_sha256": terminal_event_sha256,
        "files": {
            "README.md": readme_sha256,
            "events.jsonl": events_sha256,
            "public-key.pem": public_key_sha256,
        },
    }
    return sha256(canonical_bytes(cast(JsonValue, descriptor))).hexdigest()


def _manifest(
    *,
    project_id: str,
    first_event_sequence: int,
    last_event_sequence: int,
    created_at: datetime,
    readme_sha256: str,
    events_sha256: str,
    checkpoints_sha256: str,
    public_key_sha256: str,
) -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id=f"audit-export-{project_id}-{first_event_sequence}-{last_event_sequence}",
        artifact_kind="audit_export",
        created_at_utc=created_at,
        command_name="spanvouch admin audit export",
        code=_code_provenance(),
        package=PackageProvenance(name="spanvouch", version=__version__),
        contracts={
            "spanvouch.artifact-manifest": "1.0",
            _AUDIT_EXPORT_SCHEMA: _AUDIT_EXPORT_VERSION,
        },
        configuration=ArtifactRef(
            path="README.md",
            sha256=readme_sha256,
            media_type="text/markdown",
        ),
        runtime=_runtime_provenance(),
        outputs=(
            ArtifactRef(
                path="checkpoints.json",
                sha256=checkpoints_sha256,
                media_type="application/json",
            ),
            ArtifactRef(
                path="events.jsonl",
                sha256=events_sha256,
                media_type="application/jsonl",
            ),
            ArtifactRef(
                path="public-key.pem",
                sha256=public_key_sha256,
                media_type="application/x-pem-file",
            ),
        ),
        provider_status="not_used",
    )


def _code_provenance() -> CodeProvenance:
    build_commit = os.environ.get("SPANVOUCH_BUILD_GIT_COMMIT", "").strip()
    build_identity = os.environ.get(
        "SPANVOUCH_BUILD_REPOSITORY_IDENTITY", ""
    ).strip()
    try:
        if build_commit and build_identity:
            return CodeProvenance(
                git_commit=build_commit,
                repository_identity=build_identity,
                dirty_worktree=False,
            )
        return collect_git_provenance(Path.cwd())
    except (OSError, ValueError):
        return CodeProvenance(
            git_commit="0" * 40,
            repository_identity="local:unknown",
            dirty_worktree=True,
        )


def _runtime_provenance() -> RuntimeProvenance:
    lock = Path("uv.lock")
    dependency_lock_sha256 = _raw_sha256(lock.read_bytes()) if lock.exists() else "0" * 64
    return RuntimeProvenance(
        python=sys.version.split()[0],
        os=platform.system().lower(),
        architecture=platform.machine().lower(),
        dependency_lock_sha256=dependency_lock_sha256,
    )


def _verify_manifest_file_hashes(bundle_dir: Path, manifest: ArtifactManifest) -> None:
    references = (manifest.configuration, *manifest.outputs)
    declared_paths = {reference.path for reference in references}
    if declared_paths != {"README.md", "events.jsonl", "checkpoints.json", "public-key.pem"}:
        raise ValueError("audit export manifest does not cover required files")
    for reference in references:
        path = bundle_dir / reference.path
        if _raw_sha256(path.read_bytes()) != reference.sha256:
            raise ValueError(f"audit export file hash mismatch: {reference.path}")


def _write_bundle_no_replace(output_dir: Path, contents: dict[str, bytes]) -> None:
    if output_dir.exists():
        raise FileExistsError(f"audit export bundle already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(dir=output_dir.parent, prefix=f".{output_dir.name}.tmp-")
    )
    try:
        for filename, content in contents.items():
            (temporary / filename).write_bytes(content)
        if {path.name for path in temporary.iterdir()} != _REQUIRED_EXPORT_FILES:
            raise ValueError("audit export bundle file set is invalid")
        temporary.rename(output_dir)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _readme(project_id: str, first_event_sequence: int, last_event_sequence: int) -> str:
    return (
        "# SpanVouch audit export\n\n"
        f"Project: `{project_id}`\n\n"
        f"Covered event sequence: `{first_event_sequence}` to `{last_event_sequence}`.\n\n"
        "Verify this bundle offline with `spanvouch admin audit verify <bundle>`.\n"
    )


def _text(value: str) -> bytes:
    return (value.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n").encode(
        "utf-8"
    )


def _raw_sha256(content: bytes) -> str:
    return sha256(content).hexdigest()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("audit checkpoint timestamp must be timezone-aware")
    return parsed.astimezone(UTC)
