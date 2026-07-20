"""Approved, budgeted DeepSeek diagnosis-candidate generation CLI."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue

from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.diagnosis.prompting import DiagnosisPromptBuilder
from spanvouch.diagnosis.protocols import ChatMessage, GenerationConfig, ProviderResponse
from spanvouch.evaluation.corpus import CorpusEntry, TraceReplayRepository
from spanvouch.evaluation.experiments.config import (
    ModelEndpointConfig,
    Phase5ExperimentConfig,
    load_experiment_config,
)
from spanvouch.evaluation.experiments.diagnosis import (
    DiagnosisCandidateRepository,
    generate_and_freeze_diagnosis,
)
from spanvouch.evaluation.experiments.provider import (
    GuardedProvider,
    PaidRunAuthorization,
    ProviderConfigurationError,
    RequestIdentity,
)
from spanvouch.evaluation.phase5_live_composition import (
    LiveDiagnosisDependencies,
    compose_live_diagnosis_dependencies,
)
from spanvouch.trace.diagnostic_view import TraceProjector
from spanvouch.trace.evidence_catalog import EvidenceCatalog

_VERIFIER_INSTRUCTION = "Critique evidence sufficiency only."


@dataclass(frozen=True)
class CandidateGenerationRequest:
    config: Path
    corpus_dir: Path
    output_dir: Path
    allow_live_provider: bool
    formal_run: bool
    approved_manifest_sha256: str | None


class CandidateGenerationManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_name: str = "spanvouch.phase5-candidate-generation"
    schema_version: str = "1.0"
    experiment_id: str
    mode: str
    experiment_config_sha256: str
    corpus_manifest_sha256: str
    generator: ModelEndpointConfig
    deployment_provenance_sha256: str
    entries: tuple[CorpusEntry, ...]


@dataclass(frozen=True)
class _PreparedGeneration:
    config: Phase5ExperimentConfig
    corpus: TraceReplayRepository
    entries: tuple[CorpusEntry, ...]
    config_sha256: str
    manifest_sha256: str


def _prepare_generation(config_path: Path, corpus_dir: Path) -> _PreparedGeneration:
    config = load_experiment_config(config_path)
    config_sha256 = canonical_sha256(
        cast(JsonValue, config.model_dump(mode="json"))
    )
    corpus = TraceReplayRepository(corpus_dir)
    manifest = corpus.verify()
    if manifest.metadata.experiment_config_sha256 != config_sha256:
        raise ValueError("corpus experiment configuration does not match config")
    if manifest.metadata.mode != config.mode.value:
        raise ValueError("corpus experiment mode does not match config")
    for entry in manifest.entries:
        if CorpusEntry.from_record(corpus.load(entry.cell)) != entry:
            raise ValueError("corpus entry failed reconstructive verification")
    generation_manifest = CandidateGenerationManifest(
        experiment_id=config.experiment_id,
        mode=config.mode.value,
        experiment_config_sha256=config_sha256,
        corpus_manifest_sha256=corpus.manifest_sha256,
        generator=config.generator,
        deployment_provenance_sha256=config.live_provenance.deepseek.sha256,
        entries=manifest.entries,
    )
    return _PreparedGeneration(
        config=config,
        corpus=corpus,
        entries=manifest.entries,
        config_sha256=config_sha256,
        manifest_sha256=canonical_sha256(generation_manifest),
    )


def candidate_generation_manifest_sha256(config: Path, corpus_dir: Path) -> str:
    """Return the exact credential-free identity that must be approved."""
    return _prepare_generation(config, corpus_dir).manifest_sha256


class _DiagnosisGuardAdapter:
    def __init__(self, guard: GuardedProvider) -> None:
        self._guard = guard

    async def complete(
        self, messages: tuple[ChatMessage, ...], config: GenerationConfig
    ) -> ProviderResponse:
        return (await self._guard.complete(messages, config)).response


def _guard_for_entry(
    prepared: _PreparedGeneration,
    entry: CorpusEntry,
    dependencies: LiveDiagnosisDependencies,
) -> tuple[_DiagnosisGuardAdapter, GenerationConfig]:
    record = prepared.corpus.load(entry.cell)
    context = TraceProjector().project(record.trace)
    generation = GenerationConfig(
        model=prepared.config.generator.model,
        max_tokens=prepared.config.generator.max_tokens,
        temperature=prepared.config.generator.temperature,
        extra_body=prepared.config.generator.extra_body,
    )
    prompt = DiagnosisPromptBuilder().prepare(
        context, EvidenceCatalog.from_context(context), generation
    )
    identity = RequestIdentity.from_request(
        experiment_id=prepared.config.experiment_id,
        experiment_config_sha256=prepared.config_sha256,
        deployment_provenance_sha256=(
            prepared.config.live_provenance.deepseek.sha256
        ),
        trace_sha256=entry.trace_sha256,
        diagnosis_sha256=canonical_sha256(context),
        condition_id="diagnosis_generation",
        prompt_version=prompt.prompt_version,
        prompt_sha256=prompt.prompt_sha256,
        provider=prepared.config.generator.provider,
        model=prepared.config.generator.model,
        messages=prompt.messages,
        generation=generation,
    )
    return (
        _DiagnosisGuardAdapter(
            GuardedProvider(
                delegate=dependencies.provider,
                cache=dependencies.cache,
                ledger=dependencies.ledger,
                pricing=dependencies.pricing,
                authorization=dependencies.authorization,
                mode=prepared.config.mode,
                identity=identity,
            )
        ),
        generation,
    )


async def run_candidate_generation(
    request: CandidateGenerationRequest,
    *,
    environ: Mapping[str, str] | None = None,
    deepseek_client: httpx.AsyncClient | None = None,
) -> str:
    """Generate a matrix-consumable repository after exact manifest approval."""
    prepared = _prepare_generation(request.config, request.corpus_dir)
    if request.output_dir.exists():
        raise FileExistsError("candidate output must not already exist")
    if request.approved_manifest_sha256 != prepared.manifest_sha256:
        raise ProviderConfigurationError("approved candidate manifest does not match")
    authorization = PaidRunAuthorization(
        experiment_id=prepared.config.experiment_id,
        allow_live_provider=request.allow_live_provider,
        formal_run=request.formal_run,
        frozen_manifest_sha256=prepared.manifest_sha256,
    )
    authorization.require(prepared.config.mode)
    state_path = (
        Path(".cache/phase5")
        / f"{prepared.config.experiment_id}-candidates-{prepared.manifest_sha256[:16]}.sqlite3"
    )
    dependencies = compose_live_diagnosis_dependencies(
        prepared.config,
        authorization=authorization,
        generation_manifest_sha256=prepared.manifest_sha256,
        state_path=state_path,
        environ=os.environ if environ is None else environ,
        deepseek_client=deepseek_client,
    )
    repository = DiagnosisCandidateRepository(request.output_dir)
    for entry in prepared.entries:
        provider, generation = _guard_for_entry(prepared, entry, dependencies)
        await generate_and_freeze_diagnosis(
            corpus=prepared.corpus,
            cell=entry.cell,
            expected_corpus_manifest_sha256=prepared.corpus.manifest_sha256,
            expected_record_sha256=entry.record_sha256,
            expected_trace_sha256=entry.trace_sha256,
            provider=provider,
            generation=generation,
            repository=repository,
            verifier_instruction=_VERIFIER_INSTRUCTION,
        )
    return prepared.manifest_sha256


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spanvouch experiments candidates")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--corpus-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--allow-live-provider", action="store_true")
    parser.add_argument("--formal-run", action="store_true")
    parser.add_argument("--approved-manifest-sha256")
    parser.add_argument("--manifest-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.manifest_only:
        print(candidate_generation_manifest_sha256(arguments.config, arguments.corpus_dir))
        return 0
    request = CandidateGenerationRequest(
        config=arguments.config,
        corpus_dir=arguments.corpus_dir,
        output_dir=arguments.output_dir,
        allow_live_provider=arguments.allow_live_provider,
        formal_run=arguments.formal_run,
        approved_manifest_sha256=arguments.approved_manifest_sha256,
    )
    print(asyncio.run(run_candidate_generation(request)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
