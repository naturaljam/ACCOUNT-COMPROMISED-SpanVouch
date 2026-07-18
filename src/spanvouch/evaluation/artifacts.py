from __future__ import annotations

import ctypes
import errno
import json
import math
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
_HASH_FIELDS = frozenset(
    {
        "dependencylocksha256",
        "generationconfigsha256",
        "manifestsha256",
        "promptsha256",
        "reportsha256",
        "sha256",
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
                if not isinstance(child_key, str) or self._is_sensitive_key(child_key):
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

    def _is_sensitive_string(self, value: str, *, path: tuple[str, ...]) -> bool:
        """Run non-bypassable credential and opaque-atom scans before field shapes."""
        if self._has_credential_signature(value):
            return True
        if self._is_cryptographic_bypass(value, path=path):
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
        field = path[-1]
        normalized = "".join(ArtifactSecretClassifier._key_tokens(field))
        if normalized in _HASH_FIELDS and _SHA256.fullmatch(value):
            return True
        return field == "git_commit" and _GIT_COMMIT.fullmatch(value) is not None

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
