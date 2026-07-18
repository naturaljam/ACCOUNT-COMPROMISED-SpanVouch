from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
    """Write a complete evaluation bundle using a same-parent atomic rename."""

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
                sha256(content).hexdigest()
            if {path.name for path in temporary.iterdir()} != _REQUIRED_FILENAMES:
                raise ValueError("artifact bundle must contain exactly the required files")
            if self._destination.exists():
                raise FileExistsError(
                    f"artifact bundle destination already exists: {self._destination}"
                )
            os.rename(temporary, self._destination)
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
        _require_safe("config", config)
        _require_safe("metrics", metrics)
        _require_safe("structured_events", events)
        _require_safe("environment", environment)
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


def _require_safe(location: str, value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_name = str(key).casefold()
            if _SENSITIVE_KEY_PART.search(key_name) or (
                key_name in {"prompt", "reasoning", "hidden_reasoning"}
            ):
                raise ValueError(f"artifact bundle forbids sensitive field: {location}.{key}")
            _require_safe(f"{location}.{key}", item)
        return
    if isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _require_safe(f"{location}[{index}]", item)
        return
    if isinstance(value, str) and (
        _SENSITIVE_VALUE.search(value) or _SENSITIVE_ASSIGNMENT.search(value)
    ):
        raise ValueError(f"artifact bundle forbids sensitive value: {location}")
