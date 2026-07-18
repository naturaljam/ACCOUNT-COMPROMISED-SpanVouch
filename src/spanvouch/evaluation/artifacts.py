from __future__ import annotations

import ctypes
import errno
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

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
_SENSITIVE_KEY_PART = re.compile(
    r"(?:api[_-]?key|authorization|auth[_-]?header|(?:raw|provider)[_-]?(?:body|response))",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(r"(?:bearer\s+|sk-[A-Za-z0-9_-]{8,})", re.IGNORECASE)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?:api[_\s-]?key|authorization|password|client[_\s-]?secret|"
    r"access[_\s-]?token|credential)\s*(?:=|:)",
    re.IGNORECASE,
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
            "structured-events.jsonl": b"".join(
                canonical_bytes(event) + b"\n" for event in events
            ),
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
    return (value.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n").encode(
        "utf-8"
    )


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
        _require_safe("config", item)


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
        _require_safe("environment", item)


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    if normalized.endswith("sha256"):
        return False
    if normalized in {"header", "headers", "raw", "response", "prompt", "reasoning"}:
        return True
    return normalized.endswith(
        (
            "apikey",
            "authorization",
            "credential",
            "password",
            "prompt",
            "raw",
            "reasoning",
            "secret",
            "token",
        )
    )


def _require_safe(location: str, value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _SENSITIVE_KEY_PART.search(str(key)) or _is_sensitive_key(key):
                _unsafe_artifact_content()
            _require_safe(f"{location}.{key}", item)
        return
    if isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _require_safe(f"{location}[{index}]", item)
        return
    if isinstance(value, str) and (
        _SENSITIVE_VALUE.search(value) or _SENSITIVE_ASSIGNMENT.search(value)
    ):
        _unsafe_artifact_content()
