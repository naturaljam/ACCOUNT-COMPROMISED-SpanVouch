"""Deterministic, injectable provenance and artifact bundle construction."""

from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import JsonValue

from spanvouch.contracts.artifacts import (
    ArtifactManifest,
    ArtifactRef,
    CodeProvenance,
    DatasetProvenance,
    PackageProvenance,
    RandomnessProvenance,
    RuntimeProvenance,
)
from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.artifacts import ArtifactBundleWriter, collect_git_provenance

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
) -> Path:
    """Bind the already-written output to a fail-closed Task 14 bundle."""
    source = collector or default_collector()
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
        provider_status="not_used",
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
) -> Path:
    """Stage the report until its bundle has published successfully."""
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.name}.tmp-", suffix=".json"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    published_bundle: Path | None = None
    try:
        render_report(temporary)
        report = cast(JsonValue, json.loads(temporary.read_text(encoding="utf-8")))
        published_bundle = write_bound_bundle(
            output=output,
            report=report,
            config=config,
            command_name=command_name,
            artifact_kind=artifact_kind,
            seed=seed,
            datasets=datasets,
            bundle_dir=bundle_dir,
            artifact_id=artifact_id,
            allow_dirty=allow_dirty,
            collector=collector,
        )
        os.replace(temporary, output)
        return published_bundle
    except Exception:
        if temporary.exists():
            temporary.unlink()
        if published_bundle is not None and published_bundle.exists():
            import shutil

            shutil.rmtree(published_bundle)
        raise


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


def artifact_safe_report_payload(report: Mapping[str, object]) -> dict[str, object]:
    """Remove non-persistable prompt/provider internals before report publication.

    Pydantic readers retain their default ``None`` values for omitted optional
    provenance fields, while the on-disk report cannot be mistaken for a raw
    provider transcript or prompt record.
    """
    forbidden = {"prompt_version", "prompt_sha256", "model", "provider"}

    def clean(value: object) -> object:
        if isinstance(value, Mapping):
            return {key: clean(item) for key, item in value.items() if key not in forbidden}
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, tuple):
            return [clean(item) for item in value]
        return value

    cleaned = clean(report)
    assert isinstance(cleaned, dict)
    return cleaned


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
