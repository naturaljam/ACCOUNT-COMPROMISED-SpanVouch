# SpanVouch Phase 4 Research Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the accepted Phase 2/3 history, perform the one-time AFC-to-SpanVouch cutover, freeze IVAD Contract v1, isolate core Modules from infrastructure Adapters, and make every new evaluation artifact reproducible without changing Phase 3 behavior.

**Architecture:** Keep one `spanvouch` Python distribution. Establish the dependency direction `contracts <- trace <- diagnosis <- verification <- review`; make DeepSeek, LangGraph, and SQLite concrete Adapters; let API/CLI call review application services; and let SupportLab/evaluation consume public Interfaces without being imported by the production core. Execute the migration in gated batches so naming, contract, structural, and artifact changes are reviewed independently.

**Tech Stack:** Python `>=3.12,<3.13`, Pydantic 2, FastAPI, LangGraph, SQLite, httpx, OpenTelemetry, uv, pytest/pytest-asyncio/pytest-cov, Ruff, strict mypy, Docker Compose v2.

## Global Constraints

> **Erratum (2026-07-18):** The global immutable-evidence constraint takes precedence over any Task text that appears to place new executable commands in `docs/evaluation/phase3-verification-review.md`. That historical report must remain byte-identical to `phase3-frozen-20260718`; Phase 4 reproduction commands belong in `docs/evaluation/phase3-reproduction-runbook.md`.

- Authoritative spec: `docs/superpowers/specs/2026-07-18-phase4-research-foundation-design.md`.
- Preserve full Phase 2 and Phase 3 Git history; do not squash, rebase published feature history, or force-push.
- Integrate Phase 2 before Phase 3, freeze the accepted baseline, then create `feature/phase4-research-foundation`.
- Public system name is `SpanVouch`; research method is `IVAD`; `AFC` is historical provenance only.
- Use a one-time hard cutover: no `afc` import alias, no `afc-*` wrapper, and no `AFC_*` environment fallback.
- Publish target is `spanvouch==0.2.0`; Python remains `>=3.12,<3.13`.
- Keep one Python distribution; do not create a multi-package workspace.
- Freeze only cross-Module/public contracts; do not freeze SQLite rows, LangGraph State/Command, runtime leases, or provider SDK objects.
- Contract roots use exact identifiers `spanvouch.trace/1.0`, `spanvouch.diagnostic-context/1.0`, `spanvouch.diagnosis/1.0`, `spanvouch.verification/1.0`, `spanvouch.review/1.0`, and `spanvouch.artifact-manifest/1.0`.
- Canonical JSON is UTF-8, sorted-key, compact JSON; timestamps are UTC with `Z`; NaN/Infinity and unknown fields are rejected; hashes are lowercase SHA-256.
- Old AFC datasets, manifests, reports, and their recorded provenance remain byte-identical.
- Default tests/evaluations remain offline and must make zero provider calls.
- Do not implement AutoGen, OpsLab, CodeLab, Conformal risk control, new evidence acquisition, UI, auth/RBAC, Postgres, Redis, queues, Repair Agent, or Release Gate.
- Do not run paid DeepSeek calls or rent GPU in Phase 4.
- Maintain at least 93% total coverage, Ruff clean, strict mypy clean, Docker non-root UID/GID `10001:10001`, persistence/restart recovery, and secret hygiene.
- If a build, test, frozen-hash, secret, or behavior gate fails, stop the current batch and do not begin the next task.
- Use TDD for new behavior and characterization tests before moving existing behavior.

---

## Mandatory execution order

The plan is grouped by responsibility rather than page position. Execute Tasks strictly by number:

```text
1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8
  -> 9 -> 10 -> 11 -> 12 -> 13 -> 14 -> 15 -> 16
```

Do not start a Task until the preceding Task has an accepted commit. Each heading is unique and searchable as `### Task N:`.

## Spec coverage matrix

| Approved requirement | Implementation Tasks |
|---|---|
| Phase 2/3 sequential integration and frozen marker | 1 |
| Distribution/import hard cutover | 2 |
| CLI, environment, API, Docker, README cutover | 3 |
| Canonical serialization and typed version errors | 4 |
| Trace and Diagnostic Context contracts | 5 |
| Diagnosis contract and extensible taxonomy | 6 |
| Verification contract | 7 |
| Review contract and private runtime state | 8 |
| Deterministic verification Module | 9 |
| Semantic verification and DeepSeek Adapter | 10 |
| SQLite Adapter | 11 |
| Pure review/application/LangGraph boundaries | 12 |
| SupportLab/evaluation outer Modules and dependency tests | 13 |
| Artifact Manifest, bundle, provenance | 14–15 |
| Label-leakage and secret defenses | 10, 15–16 |
| Schemas, fixtures, ADRs, migration/research docs | 5–8, 14, 16 |
| Full behavior, evaluation, wheel, Docker, persistence and release gates | 16 |
| Paper claim discipline and Phase 5 handoff | 16 |

---

### Task 14: Implement Artifact Manifest Contract v1 and deterministic bundle writing

**Files:**
- Create: `src/spanvouch/contracts/artifacts.py`
- Create: `src/spanvouch/evaluation/artifacts.py`
- Create: `tests/contracts/test_artifact_contract.py`
- Create: `tests/evaluation/test_artifacts.py`
- Create: `schemas/v1/spanvouch.artifact-manifest-1.0.schema.json`
- Create: `tests/contracts/fixtures/v1/artifact-manifest.valid.json`

**Interfaces:**
- Consumes: canonical contract utilities, filesystem paths, Git/package/runtime metadata, report/config/dataset references.
- Produces: `ArtifactManifest`, `ArtifactRef`, `ArtifactBundleWriter.write`, `collect_git_provenance`, and a deterministic bundle containing `manifest.json`, `config.json`, `metrics.json`, `structured-events.jsonl`, `environment.txt`, and `README.md`.

- [ ] **Step 1: Write failing manifest-contract tests**

Create `tests/contracts/test_artifact_contract.py`:

```python
from datetime import UTC, datetime

import pytest

from spanvouch.contracts.artifacts import (
    ArtifactManifest,
    ArtifactRef,
    CodeProvenance,
    PackageProvenance,
    RuntimeProvenance,
)


def _manifest(*, dirty: bool = False) -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id="phase4-offline-reference",
        artifact_kind="evaluation_bundle",
        created_at_utc=datetime(2026, 7, 18, tzinfo=UTC),
        command_name="spanvouch evaluate review",
        code=CodeProvenance(
            git_commit="a" * 40,
            repository_identity="local:self-agent",
            dirty_worktree=dirty,
        ),
        package=PackageProvenance(name="spanvouch", version="0.2.0"),
        contracts={"spanvouch.verification": "1.0"},
        configuration=ArtifactRef(path="config.json", sha256="b" * 64, media_type="application/json"),
        runtime=RuntimeProvenance(
            python="3.12.10",
            os="windows",
            architecture="amd64",
            dependency_lock_sha256="c" * 64,
        ),
        outputs=(
            ArtifactRef(path="metrics.json", sha256="d" * 64, media_type="application/json"),
        ),
        provider_status="not_used",
    )


def test_manifest_has_stable_contract_identity() -> None:
    manifest = _manifest()
    assert manifest.schema_name == "spanvouch.artifact-manifest"
    assert manifest.schema_version == "1.0"


def test_release_evidence_rejects_dirty_worktree() -> None:
    with pytest.raises(ValueError, match="release evidence requires a clean worktree"):
        _manifest(dirty=True).require_release_evidence()


def test_not_used_provider_forbids_usage_or_models() -> None:
    payload = _manifest().model_dump(mode="python")
    payload["usage"] = {"requests": 1, "input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
    with pytest.raises(ValueError, match="not_used provider forbids model usage"):
        ArtifactManifest.model_validate(payload)
```

- [ ] **Step 2: Write failing bundle-writer tests**

Create `tests/evaluation/test_artifacts.py`:

```python
from pathlib import Path

from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.artifacts import ArtifactBundleWriter


def test_bundle_writer_hashes_every_required_file(tmp_path: Path, artifact_manifest) -> None:
    bundle = tmp_path / "bundle"
    writer = ArtifactBundleWriter(bundle)
    written = writer.write(
        manifest=artifact_manifest,
        config={"mode": "deterministic"},
        metrics={"status": "complete"},
        structured_events=(),
        environment="python=3.12\n",
        readme="# Reproduce\n",
    )
    assert set(path.name for path in written) == {
        "manifest.json",
        "config.json",
        "metrics.json",
        "structured-events.jsonl",
        "environment.txt",
        "README.md",
    }
    assert canonical_sha256({"mode": "deterministic"}) == next(
        ref.sha256 for ref in artifact_manifest.inputs if ref.path == "config.json"
    )
```

The shared `artifact_manifest` fixture must include the matching input reference; build it in `tests/evaluation/conftest.py` with fixed timestamps and hashes.

- [ ] **Step 3: Run RED tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contracts/test_artifact_contract.py tests/evaluation/test_artifacts.py -v
```

Expected: import failures.

- [ ] **Step 4: Implement the complete Artifact Manifest model**

`contracts/artifacts.py` must define strict frozen value objects for:

```text
ArtifactRef(path, sha256, media_type)
CodeProvenance(git_commit, repository_identity, dirty_worktree)
PackageProvenance(name, version)
DatasetProvenance(dataset_id, version, manifest_sha256, payloads)
RandomnessProvenance(seed, deterministic_flags)
ModelProvenance(provider, model, endpoint_class, generation_config_sha256, prompt_sha256)
RuntimeProvenance(python, os, architecture, dependency_lock_sha256)
UsageProvenance(requests, input_tokens, output_tokens, total_tokens)
CostProvenance(currency, basis, amount, pricing_ref)
ArtifactManifest root fields specified immediately below
```

Use this exact root shape:

```python
class ArtifactManifest(ContractRoot):
    schema_name: Literal["spanvouch.artifact-manifest"] = "spanvouch.artifact-manifest"
    schema_version: Literal["1.0"] = "1.0"
    artifact_id: str = Field(min_length=1)
    artifact_kind: str = Field(min_length=1)
    created_at_utc: datetime
    command_name: str = Field(min_length=1)
    code: CodeProvenance
    package: PackageProvenance
    contracts: dict[str, str] = Field(min_length=1)
    datasets: tuple[DatasetProvenance, ...] = ()
    configuration: ArtifactRef
    randomness: RandomnessProvenance | None = None
    models: tuple[ModelProvenance, ...] = ()
    runtime: RuntimeProvenance
    inputs: tuple[ArtifactRef, ...] = ()
    outputs: tuple[ArtifactRef, ...] = Field(min_length=1)
    metrics_schema_ref: str | None = None
    usage: UsageProvenance | None = None
    cost: CostProvenance | None = None
    parent_artifacts: tuple[str, ...] = ()
    provider_status: Literal["not_used", "used", "failed"]

    def require_release_evidence(self) -> None:
        if self.code.dirty_worktree:
            raise ValueError("release evidence requires a clean worktree")
```

Add validators that require UTC, sorted unique contract/input/output identities, valid totals, `models+usage` for `used`, and no models/usage/cost for `not_used`. Cost basis is `estimated` or `billed`; missing pricing produces no amount rather than fabricated zero cost.

- [ ] **Step 5: Implement atomic deterministic bundle writing**

`ArtifactBundleWriter` writes to a sibling temporary directory, verifies every SHA-256, then renames it into place. If the final directory exists, fail rather than overwrite release evidence. Write JSON using canonical compact JSON plus LF and text as UTF-8/LF. Never include environment values, API keys, Authorization headers, raw provider bodies, prompts, or hidden reasoning.

- [ ] **Step 6: Generate schema/fixture and run tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contracts/test_artifact_contract.py tests/evaluation/test_artifacts.py -v
uv run ruff check src tests
uv run mypy
```

Expected: all pass; checked-in schema equals current model output; fixture round-trips byte-exact.

- [ ] **Step 7: Commit the artifact contract**

```powershell
git add src/spanvouch/contracts/artifacts.py src/spanvouch/evaluation/artifacts.py tests/contracts tests/evaluation schemas/v1
git commit -m "feat: add reproducible artifact manifest and bundle"
```

---

### Task 15: Bind dataset/evaluation commands to provenance and enforce label isolation

**Files:**
- Create: `src/spanvouch/evaluation/provenance.py`
- Create: `src/spanvouch/evaluation/provider_view.py`
- Modify: `src/spanvouch/evaluation/generate_dataset.py`
- Modify: `src/spanvouch/evaluation/generate_review_dataset.py`
- Modify: `src/spanvouch/evaluation/run_diagnosis_eval.py`
- Modify: `src/spanvouch/evaluation/run_review_eval.py`
- Modify: `src/spanvouch/evaluation/diagnosis_metrics.py`
- Modify: `src/spanvouch/evaluation/review_metrics.py`
- Create: `tests/evaluation/test_provenance.py`
- Create: `tests/evaluation/test_label_isolation.py`
- Modify: `tests/review/test_secret_hygiene.py`
- Create: `evals/configs/phase4-offline-reference.json`

**Interfaces:**
- Consumes: Artifact Manifest/Bundle writer, frozen datasets, evaluator reports, Git/runtime/package metadata.
- Produces: every dataset/evaluation CLI run writes a report plus sibling `<output-name>.bundle/`; provider-facing objects cannot contain gold labels, mutation metadata, expected findings, or split identity.

- [ ] **Step 1: Add failing label-isolation test with sentinels**

Create `tests/evaluation/test_label_isolation.py`:

```python
from spanvouch.evaluation.provider_view import build_verifier_provider_view


def test_gold_and_mutation_sentinels_never_enter_provider_view(review_candidate) -> None:
    candidate = review_candidate.model_copy(
        update={"mutation_kind": "GOLD_SENTINEL_MUTATION"}
    )
    evaluator_only_expected_finding = "GOLD_SENTINEL_FINDING"
    visible = build_verifier_provider_view(candidate)
    serialized = visible.model_dump_json()
    assert "GOLD_SENTINEL_MUTATION" not in serialized
    assert evaluator_only_expected_finding not in serialized
    assert "gold" not in serialized.lower()
    assert "split" not in serialized.lower()
```

`build_verifier_provider_view(candidate: ReviewCandidate) -> ProviderVisibleVerificationInput` accepts only the provider-safe candidate, never `ReviewGoldLabel`. The evaluator retains `evaluator_only_expected_finding` outside the builder and joins it to the verifier result only after the call returns.

- [ ] **Step 2: Add failing provenance-completeness tests**

Create `tests/evaluation/test_provenance.py`:

```python
from pathlib import Path

from spanvouch.contracts.artifacts import ArtifactManifest
from spanvouch.evaluation.provenance import manifest_path_for


def test_evaluation_output_always_has_a_bound_manifest(tmp_path: Path, run_review_cli) -> None:
    output = tmp_path / "review.json"
    assert run_review_cli(("--output", str(output))) == 0
    bundle = manifest_path_for(output).parent
    manifest = ArtifactManifest.model_validate_json(
        (bundle / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.provider_status == "not_used"
    assert manifest.usage is None
    assert any(ref.sha256 for ref in manifest.outputs if ref.path == "metrics.json")
    assert manifest.code.dirty_worktree is False
```

The `run_review_cli` fixture injects fixed `CodeProvenance(git_commit="a" * 40, repository_identity="test", dirty_worktree=False)` through the provenance collector dependency; it must not inspect the developer's actual worktree.

- [ ] **Step 3: Run RED tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/evaluation/test_label_isolation.py tests/evaluation/test_provenance.py -v
```

Expected: missing provider-view/provenance modules or missing bundle.

- [ ] **Step 4: Separate provider-visible and evaluator-only views**

Create the strict type `ProviderVisibleVerificationInput` containing only sanitized Diagnostic Context, diagnosis claim/evidence, allowed selectors, and version/provenance needed by the verifier. It must have no fields named `gold_*`, `mutation_kind`, `expected_*`, `split`, or `label`.

Evaluator joins candidate IDs to labels only after verifier output is returned. Add a test that monkeypatches `ModelProvider.complete`, captures serialized messages, and searches for every sentinel before returning a fixed valid response.

- [ ] **Step 5: Collect deterministic provenance**

`evaluation/provenance.py` must collect:

- exact `git rev-parse HEAD` and `git status --porcelain`;
- package name/version;
- `uv.lock` SHA-256;
- Python/OS/architecture;
- contract name/version map;
- input dataset manifest and payload hashes;
- canonical config hash and seed;
- model/prompt metadata only when a provider is explicitly used;
- report and bundle file hashes.

Release-mode commands fail if the worktree is dirty. Tests and exploratory mode may inject `allow_dirty=True`, but the manifest must record `dirty_worktree=true` and `require_release_evidence()` must still reject it.

- [ ] **Step 6: Make every generator/evaluator produce a bundle**

All four commands retain `--output` and accept:

```text
--bundle-dir PATH       optional; defaults to <output>.bundle
--artifact-id TEXT      optional deterministic identifier
--allow-dirty-artifact  exploratory only; default false
```

The report stays at `--output`; the bundle's `metrics.json` is byte-identical to that report. Dataset generators use `artifact_kind=dataset_generation`; evaluators use `artifact_kind=evaluation_bundle`. Default deterministic commands record `provider_status=not_used` and zero provider calls without fabricating a zero-dollar cost.

- [ ] **Step 7: Add the frozen Phase 4 reference config**

Create `evals/configs/phase4-offline-reference.json`:

```json
{
  "schema_version": "1.0",
  "dataset": "evals/datasets/supportlab-review-v1",
  "source_dataset": "evals/datasets/supportlab-v1",
  "verifier": "deterministic",
  "policy_version": "supportlab-review-policy-v1",
  "seed": 20260717,
  "allow_live_api": false
}
```

The policy version remains the exact Phase 3 constant `supportlab-review-policy-v1`; changing it is outside Phase 4.

- [ ] **Step 8: Run label, secret, evaluator, and repeatability gates**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/evaluation tests/review/test_secret_hygiene.py -v
uv run spanvouch evaluate diagnosis --output .cache/phase4-diagnosis-a.json
uv run spanvouch evaluate diagnosis --output .cache/phase4-diagnosis-b.json
uv run spanvouch evaluate review --output .cache/phase4-review-a.json
uv run spanvouch evaluate review --output .cache/phase4-review-b.json
Get-FileHash .cache/phase4-diagnosis-a.json -Algorithm SHA256
Get-FileHash .cache/phase4-diagnosis-b.json -Algorithm SHA256
Get-FileHash .cache/phase4-review-a.json -Algorithm SHA256
Get-FileHash .cache/phase4-review-b.json -Algorithm SHA256
```

Expected: paired Phase 4 reports are byte-identical to each other; Phase 4 bytes may differ from the historical Phase 3 report because root contract metadata changed. Separately verify that the committed historical Phase 3 report and frozen dataset files retain their recorded hashes.

- [ ] **Step 9: Commit provenance and leakage gates**

```powershell
git add src/spanvouch/evaluation tests/evaluation tests/review/test_secret_hygiene.py evals/configs
git commit -m "feat: bind evaluations to provenance and label isolation"
```

---

### Task 16: Complete contract documentation, release candidate, and Phase 4 acceptance

**Files:**
- Create: `docs/contracts/catalog.md`
- Create: `docs/architecture/adr-002-contract-versioning.md`
- Create: `docs/architecture/adr-003-core-adapter-boundaries.md`
- Create: `docs/migrations/afc-to-spanvouch.md`
- Create: `docs/research/reproducibility.md`
- Create: `docs/research/ivad-claim-evidence-ledger.md`
- Create: `docs/evaluation/phase4-research-foundation.md`
- Modify: `README.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `.dockerignore`
- Modify: `Dockerfile`
- Modify: `compose.yaml`
- Create: `evals/reports/reference/phase4-offline-bundle/`
- Modify: `tests/test_delivery_config.py`

**Interfaces:**
- Consumes: all Phase 4 Modules, schemas, fixtures, reference config, and the frozen Phase 3 baseline.
- Produces: installable `spanvouch==0.2.0`, complete documentation, clean reference bundle, and evidence-backed Phase 4 acceptance report.

- [ ] **Step 1: Add final delivery assertions before documentation changes**

Extend `tests/test_delivery_config.py` to assert:

```python
assert project["project"]["name"] == "spanvouch"
assert project["project"]["version"] == "0.2.0"
assert project["project"]["scripts"] == {"spanvouch": "spanvouch.cli.main:main"}
assert "SPANVOUCH_DB_PATH" in compose
assert "AFC_DB_PATH" not in compose
assert "10001:10001" in docker_or_compose_runtime
assert "docs/contracts/catalog.md" in readme
assert "IVAD" in readme
```

Add schema inventory assertions that exactly six root schemas and their valid fixtures exist.

- [ ] **Step 2: Run delivery tests to identify missing release work**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_delivery_config.py -v
```

Expected: failure listing missing docs/reference bundle/CI updates.

- [ ] **Step 3: Write contract and architecture documentation**

`docs/contracts/catalog.md` must list each schema name/version, root type, producer, consumer, compatibility rule, schema file, valid fixture, and non-contract internals.

ADR-002 records canonicalization, strict readers, major/minor rules, explicit migrations, and why package/contract versions are independent. ADR-003 records the dependency direction and why DeepSeek/LangGraph/SQLite/SupportLab are Adapters or outer Modules.

`docs/migrations/afc-to-spanvouch.md` contains the exact old/new mapping, states there is no compatibility layer, lists allowed historical AFC occurrences, and explains that old artifacts retain AFC provenance.

- [ ] **Step 4: Write research reproducibility and claim ledger**

`docs/research/reproducibility.md` explains bundle layout, clean-worktree rule, dataset split/hash policy, provider metadata, cost semantics, label isolation, secret exclusions, and one exact offline reproduction command.

`docs/research/ivad-claim-evidence-ledger.md` starts with the six rows from the Phase 4 design. Mark deterministic Phase 3 evidence as an engineering regression only; mark semantic independence, Conformal risk, evidence acquisition, and OOD generalization as “needs evidence.”

- [ ] **Step 5: Build the clean offline reference bundle**

From a clean commit candidate, generate into the ignored cache directory:

```powershell
uv run spanvouch evaluate review `
  --output .cache/phase4-offline-reference/metrics.json `
  --bundle-dir .cache/phase4-offline-reference/bundle `
  --artifact-id phase4-offline-reference
```

Inspect hashes and secret scans, then move the verified bundle directory to `evals/reports/reference/phase4-offline-bundle` with one explicit filesystem move and stage it. The destination must not exist before the move. The final committed bundle contains no key, environment value, raw provider response, prompt text, or hidden reasoning.

- [ ] **Step 6: Run the complete local quality gate**

```powershell
uv sync --frozen --group dev
uv run ruff check src tests
uv run mypy
.\.venv\Scripts\python.exe -m pytest --cov=spanvouch --cov-report=term-missing
```

Expected: all tests pass, no unexpected warning is introduced, and total coverage is at least 93%.

- [ ] **Step 7: Run contract and architecture gates explicitly**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contracts tests/architecture -v
```

Expected: six schemas/fixtures match, canonical round trips are byte-stable, unknown version/field/hash failures are typed, and dependency direction is clean.

- [ ] **Step 8: Re-run frozen dataset and deterministic evaluation gates**

```powershell
uv run spanvouch dataset generate --output .cache/final-supportlab --seed 20260715
uv run spanvouch dataset generate-review --output .cache/final-review --seed 20260717
uv run spanvouch evaluate diagnosis --output .cache/final-diagnosis-a.json
uv run spanvouch evaluate diagnosis --output .cache/final-diagnosis-b.json
uv run spanvouch evaluate review --output .cache/final-review-a.json
uv run spanvouch evaluate review --output .cache/final-review-b.json
```

Expected: frozen dataset/manifest hashes equal Phase 3; paired Phase 4 reports are byte-identical; review quality rates remain `1.0`, `1.0`, `1.0`, and `0.0`; provider calls/tokens are zero.

- [ ] **Step 9: Run wheel and clean-environment smoke**

```powershell
uv build
uv run --isolated --with .\dist\spanvouch-0.2.0-py3-none-any.whl python -c "import spanvouch; print(spanvouch.__name__)"
uv run --isolated --with .\dist\spanvouch-0.2.0-py3-none-any.whl python -c "import afc"
uv run --isolated --with .\dist\spanvouch-0.2.0-py3-none-any.whl spanvouch --help
```

Expected: first and third commands pass; `import afc` fails with `ModuleNotFoundError`.

- [ ] **Step 10: Run Docker/non-root/persistence/restart/cleanup gate**

Use the exact isolated Compose procedure from Phase 3, replacing only approved names:

```text
AFC_DB_PATH -> SPANVOUCH_DB_PATH
afc.db -> spanvouch.db
afc_data -> spanvouch_data
afc-review -> spanvouch review
afc.api.app -> spanvouch.api.app
```

Expected: pinned build and health pass; runtime/data ownership are `10001:10001`; create/show/confirm succeeds; post-restart terminal GET is byte-identical; cleanup removes isolated container/network/volume.

- [ ] **Step 11: Run secret and active-old-name scans**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/review/test_secret_hygiene.py tests/test_delivery_config.py -v
rg -n "AFC_|afc-|src/afc|from afc|import afc|Agent Failure Clinic" src tests pyproject.toml Dockerfile compose.yaml .env.example README.md .github
```

Expected: tests pass and the active scan returns no hits. Run a separate repository-wide scan and classify every remaining AFC occurrence as history/frozen provenance in `docs/migrations/afc-to-spanvouch.md`.

- [ ] **Step 12: Write the Phase 4 acceptance report**

`docs/evaluation/phase4-research-foundation.md` must record:

- exact input/output commits and branch;
- Phase 3 marker and unchanged frozen hashes;
- rename inventory and allowed historical AFC list;
- test count, coverage, Ruff, mypy and warnings;
- six contract schema/fixture hashes;
- paired evaluation hashes and quality metrics;
- provider call/token count;
- SQLite concurrency/recovery evidence;
- wheel/import/CLI evidence;
- Docker UID/GID/persistence/restart/cleanup evidence;
- reference artifact manifest hash;
- label-leakage/secret scan evidence;
- known limitations and explicit statement that Phase 4 adds no paper effectiveness result.

- [ ] **Step 13: Commit documentation and release evidence**

```powershell
git add README.md docs schemas evals/reports/reference .github .dockerignore Dockerfile compose.yaml tests/test_delivery_config.py
git commit -m "docs: record Phase 4 research foundation acceptance"
```

- [ ] **Step 14: Verify clean final state without publishing**

```powershell
git status --short --branch
git log --oneline phase3-frozen-20260718..HEAD
git diff --check phase3-frozen-20260718..HEAD
```

Expected: clean worktree; task-sized commits in order; no whitespace errors. Stop here. GitHub repository rename, push, tag push, PyPI publish, container registry publish, and release creation require separate user authorization.

---

## Plan self-review checklist

- [ ] Every Phase 4 design requirement maps to Tasks 1–16.
- [ ] The plan contains no implementation of Phase 5–8 functionality.
- [ ] Every public type introduced in a later task is defined in an earlier task.
- [ ] Every Module move has a characterization test before the move and a boundary test after it.
- [ ] Old AFC artifacts are verified by hash rather than rewritten.
- [ ] Phase 4 repeated reports are compared with each other; historical Phase 3 bytes are checked separately.
- [ ] Contract package version and schema versions remain independent.
- [ ] SupportLab taxonomy is local; public taxonomy identifiers remain extensible.
- [ ] Runtime state, Commands, SQLite rows, and LangGraph State are not frozen contracts.
- [ ] All provider-capable paths remain explicit and offline by default.
- [ ] Final acceptance records exact commits, commands, hashes, metrics, warnings, and limitations.

## Execution handoff

Use **Subagent-Driven Development** for execution: one fresh implementation worker per Task, followed by requirements-conformance review and code-quality review before the next Task. Tasks 2–13 share public imports, so workers must execute them sequentially against the latest accepted commit. The current design thread must not execute this plan.

### Task 9: Extract deterministic verification as an independent Module

**Files:**
- Move: `src/spanvouch/review/evidence_verifier.py` -> `src/spanvouch/verification/deterministic.py`
- Move: `src/spanvouch/invariants/models.py` -> `src/spanvouch/verification/invariants.py`
- Move: `src/spanvouch/invariants/engine.py` -> `src/spanvouch/verification/invariant_engine.py`
- Move: `src/spanvouch/review/verdicts.py` -> `src/spanvouch/verification/verdicts.py`
- Move: `tests/review/test_evidence_verifier.py` -> `tests/verification/test_deterministic.py`
- Move: `tests/review/test_verdicts.py` -> `tests/verification/test_verdicts.py`
- Move: `tests/invariants/test_engine.py` -> `tests/verification/test_invariant_engine.py`
- Create: `tests/architecture/test_verification_boundary.py`

**Interfaces:**
- Consumes: `VerificationInput`, `VerifierReport`, trace/evidence contracts, and invariant rules supplied through the engine.
- Produces: `DeterministicVerifier.verify(request) -> VerifierReport`, `InvariantEngine`, and `merge_verifier_reports()` without any review workflow dependency.

- [ ] **Step 1: Add a failing architecture-boundary test**

Create `tests/architecture/test_verification_boundary.py`:

```python
from pathlib import Path


def test_verification_module_does_not_import_review_or_infrastructure() -> None:
    root = Path("src/spanvouch/verification")
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    forbidden = (
        "spanvouch.review",
        "fastapi",
        "langgraph",
        "sqlite3",
        "spanvouch.labs",
        "spanvouch.evaluation",
    )
    assert not {name for name in forbidden if name in source}
```

- [ ] **Step 2: Run boundary and characterization tests before moving code**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/review/test_evidence_verifier.py tests/review/test_verdicts.py tests/invariants -v
.\.venv\Scripts\python.exe -m pytest tests/architecture/test_verification_boundary.py -v
```

Expected: Phase 3 characterization tests pass; architecture test fails because the target Module is not populated.

- [ ] **Step 3: Move deterministic implementations without algorithm edits**

Move the complete `EvidenceVerifier` implementation to `verification/deterministic.py` and rename the class to `DeterministicVerifier`. Set:

```python
kind = "deterministic"
```

Move invariant value objects/engine and verdict aggregation to the new Module. Update imports to contracts and trace Modules. The deterministic check order, finding IDs/codes/severity, evidence-gap construction, supported-scope behavior, policy version, and hashing must remain byte-stable for equal new-contract inputs.

- [ ] **Step 4: Keep domain rules outside the verification core**

Do not move `invariants/supportlab.py` into core verification. It belongs under `labs/supportlab/invariants.py` in Task 13. Until then, API/evaluation composition may import the existing SupportLab rule provider, but `verification/*` may only accept rules through `InvariantEngine` construction.

- [ ] **Step 5: Update test paths/imports and prove exact findings**

The moved tests must continue asserting exact finding codes, IDs, selectors, spans, gaps, verdicts, and report hashes. Add one explicit byte-stability assertion:

```python
assert canonical_bytes(first_report) == canonical_bytes(second_report)
assert canonical_sha256(first_report) == canonical_sha256(second_report)
```

- [ ] **Step 6: Run deterministic verification suite and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/verification tests/architecture/test_verification_boundary.py tests/review/test_policy_identity.py -v
uv run ruff check src tests
uv run mypy
git add src/spanvouch/verification src/spanvouch/review src/spanvouch/invariants tests/verification tests/invariants tests/architecture tests/review
git commit -m "refactor: extract deterministic verification module"
```

Expected: all focused tests pass; no review/infrastructure import exists in verification.

---

### Task 10: Isolate semantic verification from the DeepSeek Adapter

**Files:**
- Move: `src/spanvouch/review/semantic_verifier.py` -> `src/spanvouch/verification/semantic.py`
- Move: `src/spanvouch/diagnosis/deepseek.py` -> `src/spanvouch/adapters/models/deepseek.py`
- Create: `src/spanvouch/adapters/__init__.py`
- Create: `src/spanvouch/adapters/models/__init__.py`
- Modify: `src/spanvouch/diagnosis/protocols.py`
- Modify: `src/spanvouch/diagnosis/llm_diagnoser.py`
- Move: `tests/review/test_semantic_verifier.py` -> `tests/verification/test_semantic.py`
- Move: `tests/diagnosis/test_deepseek.py` -> `tests/adapters/models/test_deepseek.py`
- Create: `tests/architecture/test_model_adapter_boundary.py`

**Interfaces:**
- Consumes: provider-neutral `ModelProvider.complete(messages, config) -> ProviderResponse` and verification contracts.
- Produces: provider-independent `SemanticVerifier` plus concrete `DeepSeekConfig`/`DeepSeekProvider` Adapter.

- [ ] **Step 1: Add failing provider-boundary tests**

Create `tests/architecture/test_model_adapter_boundary.py`:

```python
from pathlib import Path


def test_core_does_not_import_deepseek_adapter() -> None:
    roots = (
        Path("src/spanvouch/contracts"),
        Path("src/spanvouch/trace"),
        Path("src/spanvouch/diagnosis"),
        Path("src/spanvouch/verification"),
        Path("src/spanvouch/review"),
    )
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for root in roots
        for path in root.rglob("*.py")
    )
    assert "spanvouch.adapters.models.deepseek" not in source
    assert "api.deepseek.com" not in source
```

- [ ] **Step 2: Run semantic/provider characterization tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/review/test_semantic_verifier.py tests/diagnosis/test_deepseek.py tests/diagnosis/test_llm_diagnoser.py -v
```

Expected: all Phase 3 tests pass before moving code.

- [ ] **Step 3: Move the concrete provider unchanged**

Move `DeepSeekConfig`, `DeepSeekProvider`, HTTP validation, error mapping, strict response parsing, timeouts, and redaction to `adapters/models/deepseek.py`. The provider implements the existing neutral Interface:

```python
class ModelProvider(Protocol):
    async def complete(
        self,
        messages: tuple[ChatMessage, ...],
        config: GenerationConfig,
    ) -> ProviderResponse:
        raise NotImplementedError
```

`diagnosis/*` and `verification/*` import only `ModelProvider`, never the Adapter.

- [ ] **Step 4: Move SemanticVerifier and preserve safe degradation**

Move the complete semantic verifier to `verification/semantic.py`. Keep prompt bytes/fingerprint, preflight, schema parsing, allowed evidence view, invalid-output conversion, error sanitization, and these guarantees unchanged:

- invalid selectors may fail local preflight with zero provider calls;
- invalid structured output becomes `review_required/invalid_verifier_output`;
- raw provider body, hidden reasoning, credentials, and prompt content are never persisted;
- provider operational errors remain typed/sanitized and do not masquerade as semantic verdicts.

- [ ] **Step 5: Move imports and add composition-only Adapter references**

Only composition roots may import `DeepSeekProvider`: `api/app.py`, evaluator CLI composition, and explicit adapter tests. `LlmDiagnoser` and `SemanticVerifier` constructors continue accepting `ModelProvider`.

- [ ] **Step 6: Run offline semantic/provider/secret tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/verification/test_semantic.py tests/adapters/models/test_deepseek.py tests/diagnosis/test_llm_diagnoser.py tests/review/test_secret_hygiene.py tests/architecture/test_model_adapter_boundary.py -v
uv run ruff check src tests
uv run mypy
```

Expected: all pass with mocked providers and zero network calls.

- [ ] **Step 7: Commit the model Adapter boundary**

```powershell
git add src/spanvouch/verification src/spanvouch/adapters src/spanvouch/diagnosis src/spanvouch/api src/spanvouch/evals tests/verification tests/adapters tests/diagnosis tests/review tests/architecture
git commit -m "refactor: isolate semantic verification and model adapter"
```

---

### Task 11: Move SQLite persistence behind the storage Adapter

**Files:**
- Move: `src/spanvouch/review/sqlite_repository.py` -> `src/spanvouch/adapters/storage/sqlite.py`
- Move: `src/spanvouch/review/schema.py` -> `src/spanvouch/adapters/storage/sqlite_schema.py`
- Create: `src/spanvouch/adapters/storage/__init__.py`
- Modify: `src/spanvouch/review/protocols.py`
- Move: `tests/review/test_sqlite_repository.py` -> `tests/adapters/storage/test_sqlite.py`
- Move: `tests/review/test_sqlite_schema.py` -> `tests/adapters/storage/test_sqlite_schema.py`
- Create: `tests/architecture/test_storage_adapter_boundary.py`

**Interfaces:**
- Consumes: review Commands, public review contracts, private `ReviewRuntimeBundle`, and `ReviewRepository` port.
- Produces: `SQLiteReviewRepository` Adapter with unchanged schema v2, CAS, lease, idempotency, event ordering, and recovery behavior.

- [ ] **Step 1: Add failing storage-boundary tests**

Create `tests/architecture/test_storage_adapter_boundary.py`:

```python
from pathlib import Path


def test_review_core_does_not_import_sqlite() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/spanvouch/review").rglob("*.py")
    )
    assert "sqlite3" not in source
    assert "adapters.storage.sqlite" not in source
```

- [ ] **Step 2: Run persistence characterization tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/review/test_sqlite_schema.py tests/review/test_sqlite_repository.py tests/review/test_workflow_recovery.py -v
```

Expected: all pass before moving code.

- [ ] **Step 3: Keep the repository port in core**

`review/protocols.py` retains the complete `ReviewRepository` Interface. Its public method signatures remain exactly those accepted in Phase 3, updated only for new import paths. It references no SQLite types or schema constants.

- [ ] **Step 4: Move schema and repository implementation mechanically**

Move all SQL, connection creation, busy timeout, schema v2 validation, transaction/CAS/lease/idempotency logic, row decoding, and `asyncio.to_thread` boundaries into the Adapter files. Do not change schema version or support schema v1 migration in Phase 4.

The Adapter class still declares the same constructor and methods:

```python
class SQLiteReviewRepository:
    def __init__(
        self,
        database: str | Path,
        *,
        failure_injector: FailureInjector | None = None,
    ) -> None:
        value = os.fspath(database)
        if value == ":memory:" or value.startswith("file:"):
            raise ValueError(
                "review database must be a filesystem path; "
                "SQLite memory databases and file: URIs are unsupported"
            )
        self._database = Path(value)
        self._failure_injector = failure_injector

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)
```

All remaining port methods are moved without signature or semantic changes.

- [ ] **Step 5: Update composition roots only**

`api/app.py`, Docker startup composition, and storage tests may import `SQLiteReviewRepository`. Core review, diagnosis, verification, and contracts must not.

- [ ] **Step 6: Run persistence, recovery, concurrency, and boundary gates**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/adapters/storage tests/review/test_workflow_recovery.py tests/review/test_service.py tests/architecture/test_storage_adapter_boundary.py -v
```

Run the exact 20-process SQLite stability command from `docs/evaluation/phase3-reproduction-runbook.md`.

Expected: focused tests and 20/20 processes pass; schema remains v2; serialized public aggregates remain valid.

- [ ] **Step 7: Commit the storage Adapter**

```powershell
git add src/spanvouch/adapters/storage src/spanvouch/review src/spanvouch/api tests/adapters/storage tests/review tests/architecture
git commit -m "refactor: move SQLite behind review repository port"
```

---

### Task 12: Separate pure review transitions, application service, and LangGraph Adapter

**Files:**
- Rename: `src/spanvouch/review/service.py` -> `src/spanvouch/review/application.py`
- Create: `src/spanvouch/review/transitions.py`
- Move/split: `src/spanvouch/review/workflow.py` -> `src/spanvouch/adapters/frameworks/langgraph_review.py`
- Create: `src/spanvouch/adapters/frameworks/__init__.py`
- Modify: `src/spanvouch/review/protocols.py`
- Move: `tests/review/test_service.py` -> `tests/review/test_application.py`
- Move: `tests/review/test_workflow.py` -> `tests/adapters/frameworks/test_langgraph_review.py`
- Move: `tests/review/test_workflow_recovery.py` -> `tests/adapters/frameworks/test_langgraph_recovery.py`
- Create: `tests/review/test_transitions.py`
- Create: `tests/architecture/test_review_boundary.py`

**Interfaces:**
- Consumes: review contracts, Commands, `ReviewRepository`, `ReviewWorkflowRunner`, Diagnoser/Verifier ports.
- Produces: pure transition decisions, `ReviewApplication.create/get/resume/decide`, and LangGraph `ReviewWorkflowRunner` Adapter.

- [ ] **Step 1: Add pure transition tests before extracting workflow routing**

Create `tests/review/test_transitions.py`:

```python
from spanvouch.contracts.review import DiagnosisReviewCase
from spanvouch.review.transitions import ReviewRoute, next_route


def test_pending_case_routes_to_initial_verification(pending_case: DiagnosisReviewCase) -> None:
    assert next_route(pending_case) is ReviewRoute.VERIFY_INITIAL


def test_revision_count_one_never_routes_to_second_revision(revision_one_case) -> None:
    assert next_route(revision_one_case) is not ReviewRoute.REQUEST_REVISION


def test_terminal_case_routes_to_end(confirmed_case: DiagnosisReviewCase) -> None:
    assert next_route(confirmed_case) is ReviewRoute.END
```

Add required fixtures to `tests/review/factories.py` using existing factory constructors and exact statuses.

- [ ] **Step 2: Add review dependency-boundary test**

Create `tests/architecture/test_review_boundary.py`:

```python
from pathlib import Path


def test_review_core_has_no_framework_or_storage_imports() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("src/spanvouch/review").rglob("*.py")
    )
    forbidden = ("langgraph", "sqlite3", "adapters.storage", "adapters.frameworks")
    assert not {name for name in forbidden if name in source}
```

- [ ] **Step 3: Run RED tests and existing workflow characterization**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/review/test_transitions.py tests/architecture/test_review_boundary.py -v
.\.venv\Scripts\python.exe -m pytest tests/review/test_workflow.py tests/review/test_workflow_recovery.py tests/review/test_service.py -v
```

Expected: new tests fail because the split does not exist; existing tests pass.

- [ ] **Step 4: Extract pure routing and transition rules**

Create `review/transitions.py` with a closed routing enum and pure function. Move the logic currently in `_route`, `_should_request_revision`, `human_decision_transition`, and other state-only helpers without repository/provider calls:

```python
class ReviewRoute(StrEnum):
    VERIFY_INITIAL = "verify_initial"
    REQUEST_REVISION = "request_revision"
    REVISE_ONCE = "revise_once"
    VERIFY_FINAL = "verify_final"
    ROUTE_TO_HUMAN = "route_to_human"
    END = "end"


def next_route(case: DiagnosisReviewCase) -> ReviewRoute:
    if case.status in {ReviewStatus.CONFIRMED, ReviewStatus.CORRECTED, ReviewStatus.REJECTED}:
        return ReviewRoute.END
    if case.status in {ReviewStatus.PENDING_VERIFICATION, ReviewStatus.VERIFYING}:
        return (
            ReviewRoute.VERIFY_INITIAL
            if case.current_revision_number == 0
            else ReviewRoute.VERIFY_FINAL
        )
    if case.status is ReviewStatus.REVISION_REQUESTED:
        return ReviewRoute.REQUEST_REVISION
    if case.status is ReviewStatus.REVISING:
        return ReviewRoute.REVISE_ONCE
    return ReviewRoute.ROUTE_TO_HUMAN
```

Keep any report-dependent revision decision as a separate pure function taking the case and current reports; do not hide provider calls in transitions.

- [ ] **Step 5: Rename ReviewService to ReviewApplication**

Preserve the complete Phase 3 create/get/resume/decide logic, create-reservation heartbeat, idempotency, human correction building, and live-API guard. Public signatures remain:

```python
class ReviewApplication:
    async def create(
        self,
        trace: TraceIR,
        *,
        diagnoser: str,
        verification_mode: VerificationMode,
        idempotency_key: str,
    ) -> DiagnosisReviewDetail:
        raise NotImplementedError

    async def get(self, case_id: str) -> DiagnosisReviewDetail:
        raise NotImplementedError

    async def resume(
        self,
        case_id: str,
        *,
        allow_live_api: bool = False,
    ) -> DiagnosisReviewDetail:
        raise NotImplementedError

    async def decide(
        self,
        case_id: str,
        decision: HumanDecisionDraft,
        *,
        idempotency_key: str,
    ) -> DiagnosisReviewDetail:
        raise NotImplementedError
```

These are the exact application signatures after the Task 6 extensible `diagnoser` seam. API request validation may restrict configured diagnoser identifiers, but the core application accepts the validated string.

- [ ] **Step 6: Move LangGraph compilation and provider lifecycle into the Adapter**

Move `ReviewWorkflowState`, graph compilation, nodes, lease heartbeat, provider lifecycle, recovery and durable effect coordination into `adapters/frameworks/langgraph_review.py`. Rename the concrete class `LangGraphReviewWorkflow`. It implements:

```python
class ReviewWorkflowRunner(Protocol):
    async def run(self, case_id: str) -> DiagnosisReviewDetail:
        raise NotImplementedError

    async def resume(self, case_id: str) -> DiagnosisReviewDetail:
        raise NotImplementedError
```

LangGraph state remains a routing hint, never a public contract or recovery record.

- [ ] **Step 7: Update API composition and run behavior/recovery tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/review/test_transitions.py tests/review/test_application.py tests/adapters/frameworks tests/api/test_diagnosis_reviews.py tests/review/test_secret_hygiene.py tests/architecture/test_review_boundary.py -v
uv run ruff check src tests
uv run mypy
```

Expected: one-revision bound, provider failure routing, lease ownership, resumption, human decision, event ordering, and secret hygiene all match Phase 3.

- [ ] **Step 8: Commit application/framework separation**

```powershell
git add src/spanvouch/review src/spanvouch/adapters/frameworks src/spanvouch/api tests/review tests/adapters/frameworks tests/api tests/architecture
git commit -m "refactor: separate review application and LangGraph adapter"
```

---

### Task 13: Move SupportLab/evaluation out of the production core and enforce dependency direction

**Files:**
- Move: `src/spanvouch/supportlab/` -> `src/spanvouch/labs/supportlab/`
- Move: `src/spanvouch/invariants/supportlab.py` -> `src/spanvouch/labs/supportlab/invariants.py`
- Move: `src/spanvouch/evals/` -> `src/spanvouch/evaluation/`
- Move: `tests/supportlab/` -> `tests/labs/supportlab/`
- Move: `tests/evals/` -> `tests/evaluation/`
- Create: `tests/architecture/test_dependency_direction.py`
- Modify: `src/spanvouch/cli/main.py`
- Modify: `src/spanvouch/api/app.py`

**Interfaces:**
- Consumes: public core contracts/ports and existing SupportLab/evaluator behavior.
- Produces: `labs.supportlab` and `evaluation` consumer Modules; production core remains independent.

- [ ] **Step 1: Add a complete import-direction test**

Create `tests/architecture/test_dependency_direction.py`:

```python
from pathlib import Path


CORE_ROOTS = ("contracts", "trace", "diagnosis", "verification", "review")


def _source(root: str) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path(f"src/spanvouch/{root}").rglob("*.py")
    )


def test_production_core_never_imports_labs_or_evaluation() -> None:
    source = "\n".join(_source(root) for root in CORE_ROOTS)
    assert "spanvouch.labs" not in source
    assert "spanvouch.evaluation" not in source


def test_contracts_never_import_higher_or_infrastructure_modules() -> None:
    source = _source("contracts")
    forbidden = (
        "spanvouch.trace",
        "spanvouch.diagnosis",
        "spanvouch.verification",
        "spanvouch.review",
        "spanvouch.adapters",
        "fastapi",
        "langgraph",
        "sqlite3",
    )
    assert not {name for name in forbidden if name in source}
```

- [ ] **Step 2: Run the boundary test and current lab/evaluation tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/architecture/test_dependency_direction.py tests/supportlab tests/evals -v
```

Expected: existing tests pass and the boundary test exposes current SupportLab coupling.

- [ ] **Step 3: Move SupportLab and evaluation mechanically**

Use `git mv` for directories and update imports. Do not change dataset seeds, scenario definitions, failure injection, labels, metric formulas, evaluator thresholds, or report ordering.

- [ ] **Step 4: Introduce an API composition factory outside core**

Because the demo API uses SupportLab rules, place default composition in `spanvouch/api/composition.py` or `spanvouch/labs/supportlab/composition.py`; `review`, `verification`, `diagnosis`, and `trace` remain unaware of it. `api/app.py` may call this composition factory because API is an outer adapter.

- [ ] **Step 5: Update the CLI router and delivery references**

`cli/main.py` imports evaluator entrypoints from `spanvouch.evaluation`. README and delivery tests use only public CLI commands, not internal module paths.

- [ ] **Step 6: Run all labs/evaluation/boundary tests and frozen hashes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/labs tests/evaluation tests/architecture -v
uv run spanvouch dataset generate --output .cache/phase4-supportlab --seed 20260715
uv run spanvouch dataset generate-review --output .cache/phase4-review --seed 20260717
```

Compare regenerated manifests and payload hashes to the frozen values in `docs/evaluation/phase3-frozen-baseline.md`.

Expected: hashes match exactly; boundary tests pass.

- [ ] **Step 7: Commit lab/evaluation separation**

```powershell
git add src/spanvouch/labs src/spanvouch/evaluation src/spanvouch/cli src/spanvouch/api tests/labs tests/evaluation tests/architecture README.md
git commit -m "refactor: isolate labs and evaluation from production core"
```

---

### Task 5: Freeze Trace and Diagnostic Context contracts

**Files:**
- Create: `src/spanvouch/contracts/trace.py`
- Create: `src/spanvouch/trace/__init__.py`
- Move: `src/spanvouch/trace_ir/mapper.py` -> `src/spanvouch/trace/mapper.py`
- Move: `src/spanvouch/trace_ir/repository.py` -> `src/spanvouch/trace/repository.py`
- Move/split: `src/spanvouch/diagnosis/trace_view.py` -> `src/spanvouch/trace/diagnostic_view.py`
- Move: `src/spanvouch/diagnosis/evidence.py` -> `src/spanvouch/trace/evidence_catalog.py`
- Create: `tests/contracts/test_trace_contract.py`
- Move: `tests/trace_ir/` -> `tests/trace/`
- Move: `tests/diagnosis/test_trace_view.py` -> `tests/trace/test_diagnostic_view.py`
- Move: `tests/diagnosis/test_evidence.py` -> `tests/trace/test_evidence_catalog.py`
- Create: `schemas/v1/spanvouch.trace-1.0.schema.json`
- Create: `schemas/v1/spanvouch.diagnostic-context-1.0.schema.json`
- Create: `tests/contracts/fixtures/v1/trace.valid.json`
- Create: `tests/contracts/fixtures/v1/diagnostic-context.valid.json`

**Interfaces:**
- Consumes: canonical functions from Task 4 and the existing Phase 3 `TraceIR`, `TraceSpan`, `DiagnosticSpan`, `DiagnosticTraceView`, sanitizer, mapper, repository, and `EvidenceCatalog` behavior.
- Produces: `TraceIR`, `TraceSpan`, `DiagnosticContext`, `DiagnosticTraceView`, `TraceProjector.project(trace) -> DiagnosticContext`, and `EvidenceCatalog.from_context(context)`.

- [ ] **Step 1: Add failing root-schema and projection tests**

Create `tests/contracts/test_trace_contract.py`:

```python
from datetime import UTC, datetime

from spanvouch.contracts.trace import DiagnosticContext, TraceIR, TraceSpan
from spanvouch.contracts.versioning import canonical_json
from spanvouch.trace.diagnostic_view import TraceProjector


def _trace() -> TraceIR:
    now = datetime(2026, 7, 18, tzinfo=UTC)
    return TraceIR(
        trace_id="trace-1",
        run_id="run-1",
        spans=[
            TraceSpan(
                trace_id="trace-1",
                span_id="root",
                parent_span_id=None,
                name="agent",
                kind="agent",
                status="error",
                started_at=now,
                ended_at=now,
                attributes={"error.type": "tool_error", "secret": "must-not-pass"},
            )
        ],
    )


def test_trace_contract_has_stable_identity() -> None:
    trace = _trace()
    assert trace.schema_name == "spanvouch.trace"
    assert trace.schema_version == "1.0"
    assert '"schema_name":"spanvouch.trace"' in canonical_json(trace)


def test_projector_returns_bound_sanitized_context() -> None:
    context = TraceProjector().project(_trace())
    assert isinstance(context, DiagnosticContext)
    assert context.schema_name == "spanvouch.diagnostic-context"
    assert context.trace_id == "trace-1"
    assert context.run_id == "run-1"
    assert context.view.spans[0].attributes == {"error.type": "tool_error"}
```

- [ ] **Step 2: Run focused tests to establish RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contracts/test_trace_contract.py -v
```

Expected: import failures for `contracts.trace` and `TraceProjector`.

- [ ] **Step 3: Move strict trace value objects into the contract Module**

Create `src/spanvouch/contracts/trace.py` by moving the complete existing `SpanKind`, `SpanStatus`, `TraceSpan`, `TraceIR`, `DiagnosticSpan`, and `DiagnosticTraceView` definitions without omitting a field, validator, or method. Change `TraceSpan`, `DiagnosticSpan`, and `DiagnosticTraceView` to inherit `ContractModel`; change `TraceIR` to inherit `ContractRoot`, replace its old lone `schema_version` field with the two fields below, and keep `validate_span_tree()` plus `span_by_id()` byte-for-byte except for imports.

```python
from typing import Literal

from pydantic import Field

from spanvouch.contracts.versioning import ContractModel, ContractRoot


class TraceIR(ContractRoot):
    schema_name: Literal["spanvouch.trace"] = "spanvouch.trace"
    schema_version: Literal["1.0"] = "1.0"
    trace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    spans: list[TraceSpan] = Field(min_length=1)


class DiagnosticContext(ContractRoot):
    schema_name: Literal["spanvouch.diagnostic-context"] = (
        "spanvouch.diagnostic-context"
    )
    schema_version: Literal["1.0"] = "1.0"
    trace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    view: DiagnosticTraceView
```

The implementation file contains the full moved definitions. The snippet shows only the exact new root metadata and `DiagnosticContext`; it does not authorize shortening the moved Phase 3 classes.

- [ ] **Step 4: Create the projection Interface and implementation**

In `src/spanvouch/trace/diagnostic_view.py`, retain the complete Phase 3 sanitizer and add:

```python
from typing import Protocol

from spanvouch.contracts.trace import DiagnosticContext, DiagnosticTraceView, TraceIR


class TraceProjectorPort(Protocol):
    def project(self, trace: TraceIR) -> DiagnosticContext:
        raise NotImplementedError


class TraceProjector:
    def project(self, trace: TraceIR) -> DiagnosticContext:
        view = DiagnosticTraceView.from_trace(trace)
        return DiagnosticContext(
            trace_id=trace.trace_id,
            run_id=trace.run_id,
            view=view,
        )
```

Move the complete `DiagnosticTraceView.from_trace()` behavior into `TraceProjector.project()` and remove the classmethod. Update every caller to construct context through `TraceProjector`; there is no old `afc` compatibility layer.

- [ ] **Step 5: Move EvidenceCatalog behind DiagnosticContext**

Keep the Phase 3 selector ordering, value hashing, and `resolve()` behavior. Add:

```python
@classmethod
def from_context(cls, context: DiagnosticContext) -> "EvidenceCatalog":
    return cls.from_view(context.view)
```

All diagnosis and verification callers must use `context.view` and `EvidenceCatalog.from_context(context)` at public seams.

- [ ] **Step 6: Generate checked-in schemas and canonical fixtures**

Use `TraceIR.model_json_schema()` and `DiagnosticContext.model_json_schema()` in a one-off reviewed generation step. Write JSON with sorted keys, two-space indentation, UTF-8, LF. Fixtures use the test object above and canonical compact JSON plus LF. Do not add a runtime schema-generation dependency.

- [ ] **Step 7: Run contract, sanitizer, trace, and secret tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contracts/test_trace_contract.py tests/trace tests/review/test_secret_hygiene.py -v
uv run ruff check src tests
uv run mypy
```

Expected: all pass; sanitizer limits and secret redaction remain unchanged; schemas match current model output.

- [ ] **Step 8: Commit the trace boundary**

```powershell
git add src/spanvouch/contracts/trace.py src/spanvouch/trace tests/contracts tests/trace schemas/v1
git commit -m "refactor: freeze trace and diagnostic context contracts"
```

---

### Task 6: Freeze the Diagnosis contract and open the taxonomy seam

**Files:**
- Create: `src/spanvouch/contracts/diagnosis.py`
- Modify: `src/spanvouch/diagnosis/protocols.py`
- Rename: `src/spanvouch/diagnosis/service.py` -> `src/spanvouch/diagnosis/engine.py`
- Modify: `src/spanvouch/diagnosis/rule_diagnoser.py`
- Modify: `src/spanvouch/diagnosis/llm_diagnoser.py`
- Modify: `src/spanvouch/failure_types.py`
- Create: `tests/contracts/test_diagnosis_contract.py`
- Modify: `tests/diagnosis/test_diagnosis_models.py`
- Modify: `tests/test_failure_types.py`
- Create: `schemas/v1/spanvouch.diagnosis-1.0.schema.json`
- Create: `tests/contracts/fixtures/v1/diagnosis.valid.json`

**Interfaces:**
- Consumes: `DiagnosticContext`, `EvidenceCatalog`, and Phase 3 diagnosis behavior.
- Produces: `TaxonomyRef`, extensible string identifiers, `DiagnosisDecision`, `DiagnosisExecution`, `DiagnosisReport`, and `Diagnoser.diagnose(context, evidence) -> DiagnosisExecution`.

- [ ] **Step 1: Write failing contract and extensibility tests**

Create `tests/contracts/test_diagnosis_contract.py` with:

```python
from spanvouch.contracts.diagnosis import (
    DiagnosisDecision,
    DiagnosisExecution,
    DiagnosisProvenance,
    DiagnosisReport,
    TaxonomyRef,
)


def test_diagnosis_contract_accepts_namespaced_future_taxonomy() -> None:
    decision = DiagnosisDecision(
        status="diagnosed",
        failure_type="deadlock_cycle",
        critical_span_ids=("span-1",),
        causal_chain=(
            {
                "stage": "cause",
                "statement": "two workers wait on each other",
                "evidence_ids": ("ev-1",),
            },
        ),
        evidence=(
            {
                "evidence_id": "ev-1",
                "span_id": "span-1",
                "field_path": "attributes.waits_for",
                "observed_value": "worker-2",
                "value_sha256": "1" * 64,
                "description": "wait edge",
            },
        ),
        confidence=0.8,
    )
    execution = DiagnosisExecution(
        decision=decision,
        provenance=DiagnosisProvenance(
            taxonomy=TaxonomyRef(taxonomy_id="opslab", taxonomy_version="1.0"),
            diagnoser_version="rules-v2",
        ),
    )
    report = DiagnosisReport.from_execution(
        trace_id="t1", run_id="r1", diagnoser="rules", execution=execution
    )
    assert report.schema_name == "spanvouch.diagnosis"
    assert report.failure_type == "deadlock_cycle"
    assert report.provenance.taxonomy.taxonomy_id == "opslab"


def test_diagnosed_state_still_requires_grounding() -> None:
    try:
        DiagnosisDecision(
            status="diagnosed",
            failure_type="x",
            confidence=0.5,
        )
    except ValueError as error:
        assert "critical spans, claims, and evidence" in str(error)
    else:
        raise AssertionError("ungrounded diagnosis was accepted")
```

- [ ] **Step 2: Run focused tests to establish RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contracts/test_diagnosis_contract.py -v
```

Expected: import failure for `contracts.diagnosis`.

- [ ] **Step 3: Move the full Phase 3 diagnosis model set and add root metadata**

Move all enums/value objects/validators from `diagnosis/models.py` into `contracts/diagnosis.py`, then make the following exact contract changes:

```python
class TaxonomyRef(ContractModel):
    taxonomy_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_.-]*$")
    taxonomy_version: str = Field(min_length=1)


class DiagnosisProvenance(ContractModel):
    taxonomy: TaxonomyRef
    diagnoser_version: str = Field(min_length=1)
    ruleset_version: str | None = None
    prompt_version: str | None = None
    prompt_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    model: str | None = None
    provider: str | None = None


class DiagnosisDecision(ContractModel):
    status: DiagnosisStatus
    failure_type: str | None = Field(default=None, min_length=1)
    critical_span_ids: tuple[str, ...] = ()
    causal_chain: tuple[DiagnosisClaim, ...] = Field(default=(), max_length=3)
    evidence: tuple[EvidenceRef, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    abstain_reason: AbstainReason | None = None
    # Preserve uniqueness, evidence binding, and state validators.
    # For diagnosed state require a non-empty failure_type, not a SupportLab enum.


class DiagnosisReport(DiagnosisDecision):
    schema_name: Literal["spanvouch.diagnosis"] = "spanvouch.diagnosis"
    schema_version: Literal["1.0"] = "1.0"
    trace_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    diagnoser: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)
    provenance: DiagnosisProvenance
    usage: ProviderUsage | None = None

    @classmethod
    def from_execution(
        cls,
        *,
        trace_id: str,
        run_id: str,
        diagnoser: str,
        execution: DiagnosisExecution,
    ) -> "DiagnosisReport":
        return cls(
            trace_id=trace_id,
            run_id=run_id,
            diagnoser=diagnoser,
            provenance=execution.provenance,
            usage=execution.usage,
            **execution.decision.model_dump(mode="python"),
        )
```

Use constants `SHA256_PATTERN = r"^[0-9a-f]{64}$"` and `IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_.-]*$"` from one contract utility location.

- [ ] **Step 4: Keep SupportLab validation local**

`src/spanvouch/failure_types.py` and `labs/supportlab` continue to own the closed SupportLab enum and `SUPPORTED_DIAGNOSIS_FAILURE_TYPES`. `RuleDiagnoser`, SupportLab evaluator, and human correction policy validate their local taxonomy before building the open contract. The public contract itself must accept the OpsLab test value above.

Every current Phase 3 provenance instance becomes:

```python
taxonomy=TaxonomyRef(taxonomy_id="supportlab", taxonomy_version="1.0")
```

Existing serialized failure values such as `invalid_argument` and `no_failure` stay unchanged strings.

- [ ] **Step 5: Deepen the Diagnoser Interface**

Change `diagnosis/protocols.py` to:

```python
class Diagnoser(Protocol):
    kind: str
    version_fingerprint: str

    async def diagnose(
        self,
        context: DiagnosticContext,
        evidence: EvidenceCatalog,
    ) -> DiagnosisExecution:
        raise NotImplementedError
```

Update `RevisionCapableDiagnoser` with the same `DiagnosticContext` signature. Rename `DiagnosisService` to `DiagnosisEngine`, update every import in the same commit, and create no `DiagnosisService` alias.

- [ ] **Step 6: Generate schema/fixture and run diagnosis regression**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contracts/test_diagnosis_contract.py tests/diagnosis tests/test_failure_types.py tests/api/test_diagnoses.py -v
uv run ruff check src tests
uv run mypy
```

Expected: future taxonomy test passes; every SupportLab scope guard remains enforced; diagnosis default behavior and zero-provider tests remain unchanged.

- [ ] **Step 7: Commit the diagnosis contract**

```powershell
git add src/spanvouch/contracts/diagnosis.py src/spanvouch/diagnosis src/spanvouch/failure_types.py tests/contracts tests/diagnosis tests/test_failure_types.py tests/api schemas/v1
git commit -m "refactor: freeze extensible diagnosis contract"
```

---

### Task 7: Freeze the Verification contract

**Files:**
- Create: `src/spanvouch/contracts/verification.py`
- Create: `src/spanvouch/verification/__init__.py`
- Create: `src/spanvouch/verification/protocols.py`
- Move: public verification types out of `src/spanvouch/review/models.py`
- Create: `tests/contracts/test_verification_contract.py`
- Modify: `tests/review/test_models.py`
- Create: `schemas/v1/spanvouch.verification-1.0.schema.json`
- Create: `tests/contracts/fixtures/v1/verification.valid.json`

**Interfaces:**
- Consumes: Diagnosis and Diagnostic Context contracts.
- Produces: `VerificationInput`, `VerificationFinding`, `EvidenceGap`, `VerifierProvenance`, `OperationalErrorMetadata`, `VerifierReport`, and `Verifier.verify(request) -> VerifierReport`.

- [ ] **Step 1: Write failing contract invariants**

Create `tests/contracts/test_verification_contract.py`:

```python
from datetime import UTC, datetime

import pytest

from spanvouch.contracts.verification import VerifierReport


def _base() -> dict[str, object]:
    now = datetime(2026, 7, 18, tzinfo=UTC)
    return {
        "verifier_run_id": "vr-1",
        "revision_number": 0,
        "report_sha256": "1" * 64,
        "verifier_kind": "deterministic",
        "verdict": "verified",
        "provenance": {
            "verifier_kind": "deterministic",
            "verifier_version": "det-v1",
            "policy_version": "policy-v1",
        },
        "started_at": now,
        "completed_at": now,
    }


def test_verifier_report_is_a_versioned_root() -> None:
    report = VerifierReport(**_base())
    assert report.schema_name == "spanvouch.verification"
    assert report.schema_version == "1.0"


def test_verified_report_rejects_evidence_gaps() -> None:
    payload = _base()
    payload["evidence_gaps"] = (
        {
            "gap_id": "g1",
            "finding_code": "semantic_support_missing",
            "required_evidence_kind": "causal_support",
            "instruction": "supply causal evidence",
        },
    )
    with pytest.raises(ValueError, match="verified verdict forbids evidence gaps"):
        VerifierReport(**payload)
```

- [ ] **Step 2: Run RED test**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contracts/test_verification_contract.py -v
```

Expected: import failure.

- [ ] **Step 3: Move the complete verification model cluster**

Move `VerificationMode`, `VerifierVerdict`, `FindingSeverity`, `FindingCode`, `VerificationFinding`, `EvidenceGap`, `VerifierProvenance`, `OperationalErrorMetadata`, `ReviewInputSnapshot`, `VerificationInput`, and `VerifierReport` from `review/models.py` to `contracts/verification.py`. Preserve every Phase 3 field validator and model validator. Change extensible identifiers to constrained strings at the public seam:

```python
class VerifierProvenance(ContractModel):
    verifier_kind: str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)
    verifier_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    prompt_version: str | None = None
    prompt_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    model: str | None = None
    provider: str | None = None


class VerifierReport(ContractRoot):
    schema_name: Literal["spanvouch.verification"] = "spanvouch.verification"
    schema_version: Literal["1.0"] = "1.0"
    # Move all Phase 3 report fields and validators unchanged.
```

Keep `FindingCode` as the v1 finding vocabulary. It can receive a contract minor/major revision later; unlike provider/model identity, it directly determines review policy semantics.

- [ ] **Step 4: Define the isolated Verifier Interface**

Create `verification/protocols.py`:

```python
from typing import Protocol

from spanvouch.contracts.verification import VerificationInput, VerifierReport


class Verifier(Protocol):
    kind: str
    version_fingerprint: str

    async def verify(self, request: VerificationInput) -> VerifierReport:
        raise NotImplementedError
```

Review code imports this Interface; verification code must not import review Commands, repository, workflow, or application service.

- [ ] **Step 5: Generate schema/fixture and update imports**

Update all callers to import verification contracts from `spanvouch.contracts.verification`. Do not retain re-export cycles from `review.models`. Generate and check in the schema and canonical valid fixture.

- [ ] **Step 6: Run contract and verifier-model regression**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contracts/test_verification_contract.py tests/review/test_models.py tests/review/test_policy_identity.py -v
uv run ruff check src tests
uv run mypy
```

Expected: all Phase 3 report invariants pass under the new import path.

- [ ] **Step 7: Commit the verification contract**

```powershell
git add src/spanvouch/contracts/verification.py src/spanvouch/verification tests/contracts tests/review schemas/v1
git commit -m "refactor: freeze verification contract"
```

---

### Task 8: Freeze the public Review contract and isolate runtime state

**Files:**
- Create: `src/spanvouch/contracts/review.py`
- Modify: `src/spanvouch/review/models.py`
- Modify: `src/spanvouch/review/commands.py`
- Create: `src/spanvouch/review/runtime.py`
- Create: `tests/contracts/test_review_contract.py`
- Modify: `tests/review/test_models.py`
- Create: `schemas/v1/spanvouch.review-1.0.schema.json`
- Create: `tests/contracts/fixtures/v1/review.valid.json`

**Interfaces:**
- Consumes: Diagnosis and Verification contracts.
- Produces: versioned `DiagnosisReviewDetail` plus public `DiagnosisReviewCase`, `DiagnosisRevision`, `HumanReviewDecision`, and `WorkflowEvent`; keeps `ReviewRuntimeBundle` private to `review/runtime.py`.

- [ ] **Step 1: Add failing public/private boundary tests**

Create `tests/contracts/test_review_contract.py`:

```python
import importlib

from spanvouch.contracts.review import DiagnosisReviewDetail


def test_review_detail_is_the_versioned_public_root(review_detail) -> None:
    detail = DiagnosisReviewDetail.model_validate(review_detail.model_dump(mode="python"))
    assert detail.schema_name == "spanvouch.review"
    assert detail.schema_version == "1.0"


def test_runtime_bundle_is_not_exported_as_a_contract() -> None:
    module = importlib.import_module("spanvouch.contracts.review")
    assert not hasattr(module, "ReviewRuntimeBundle")
```

Use the existing `tests/review/factories.py` fixture or add a typed `review_detail` fixture there; do not build an unrelated second factory.

- [ ] **Step 2: Run RED test**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contracts/test_review_contract.py -v
```

Expected: import failure.

- [ ] **Step 3: Move public aggregate types and validators**

Move these complete definitions from `review/models.py` into `contracts/review.py`: `ReviewStatus`, `DecisionAction`, `RevisionOrigin`, `WorkflowEventType`, `DiagnosisRevision`, `CorrectionClaim`, `DiagnosisCorrectionDraft`, `HumanDecisionDraft`, `HumanReviewDecision`, `DiagnosisReviewCase`, `WorkflowEvent`, `resume_requires_live_api`, and `DiagnosisReviewDetail`.

Change `DiagnosisReviewCase.diagnoser` from the closed `DiagnoserKind` annotation to `str = Field(min_length=1, pattern=IDENTIFIER_PATTERN)`. API/composition policy still restricts configured values to `rules` and `deepseek`; the public review contract remains extensible.

Make only the aggregate root versioned:

```python
class DiagnosisReviewDetail(ContractRoot):
    schema_name: Literal["spanvouch.review"] = "spanvouch.review"
    schema_version: Literal["1.0"] = "1.0"
    case: DiagnosisReviewCase
    revisions: tuple[DiagnosisRevision, ...]
    verifier_reports: tuple[VerifierReport, ...] = ()
    events: tuple[WorkflowEvent, ...] = ()
    decision: HumanReviewDecision | None = None

    @computed_field
    @property
    def resume_requires_live_api(self) -> bool:
        return resume_requires_live_api(self.case, self.verifier_reports)
```

Preserve all Phase 3 validators including one evidence revision, terminal decision agreement, correction requirements, sorted selectors, report hash, and revision chain.

- [ ] **Step 4: Make runtime state explicitly private**

Create `review/runtime.py` and move `ReviewRuntimeBundle` there unchanged. It may reference contracts, lease owner, and lease expiry. It must not have `schema_name` or appear in JSON Schema/fixtures.

Keep Commands in `review/commands.py`; they are internal use-case inputs and are not exported from `contracts.review`.

- [ ] **Step 5: Generate schema/fixture and update all imports**

Update API response annotations, repository ports, service/application code, tests, and SQLite decoding to use `contracts.review`. Generate the review JSON Schema and a valid terminal detail fixture from existing factories.

- [ ] **Step 6: Run review model/API/SQLite regression**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contracts/test_review_contract.py tests/review/test_models.py tests/review/test_service.py tests/review/test_sqlite_repository.py tests/api/test_diagnosis_reviews.py -v
uv run ruff check src tests
uv run mypy
```

Expected: all pass; public API JSON gains only the approved root schema metadata; internal SQLite row/schema is not treated as Contract v1.

- [ ] **Step 7: Commit the review contract boundary**

```powershell
git add src/spanvouch/contracts/review.py src/spanvouch/review tests/contracts tests/review tests/api schemas/v1
git commit -m "refactor: freeze public review contract"
```

---

## Dependency and task map

```text
Task 1  integrated frozen baseline
  -> Task 2 Python namespace cutover
  -> Task 3 public CLI/config/container cutover
  -> Task 4 canonical/versioning kernel
  -> Task 5 trace + diagnostic-context contracts
  -> Task 6 diagnosis contract + extensible taxonomy
  -> Task 7 verification contract
  -> Task 8 review contract
  -> Task 9 deterministic verification Module
  -> Task 10 semantic verifier + model Adapter
  -> Task 11 SQLite storage Adapter
  -> Task 12 review application + LangGraph Adapter
  -> Task 13 labs/evaluation boundary migration
  -> Task 14 Artifact Manifest and bundle
  -> Task 15 evaluator provenance + leakage gates
  -> Task 16 release candidate and Phase 4 acceptance
```

Tasks are intentionally sequential because they move shared public types. Parallelize only test inventory, documentation checks, or read-only review; do not let multiple workers edit the same contract or migration batch concurrently.

## Target file responsibility map

```text
src/spanvouch/contracts/versioning.py       strict root metadata, canonical bytes/hash, typed errors
src/spanvouch/contracts/trace.py            TraceIR, TraceSpan, DiagnosticContext value contracts
src/spanvouch/contracts/diagnosis.py        claim/evidence/diagnosis/provenance contracts
src/spanvouch/contracts/verification.py     verifier report/finding/gap contracts
src/spanvouch/contracts/review.py           public review aggregate/decision/event contracts
src/spanvouch/contracts/artifacts.py        ArtifactManifest and bundle references
src/spanvouch/trace/mapper.py               OpenTelemetry-to-TraceIR mapping
src/spanvouch/trace/diagnostic_view.py      sanitized trace projection
src/spanvouch/trace/evidence_catalog.py     selector resolution and evidence hashing
src/spanvouch/trace/repository.py           trace repository Interface and in-memory implementation
src/spanvouch/diagnosis/protocols.py         Diagnoser and revision Interfaces
src/spanvouch/diagnosis/engine.py            diagnosis application service
src/spanvouch/verification/protocols.py      Verifier Interface
src/spanvouch/verification/deterministic.py  deterministic evidence/invariant verification
src/spanvouch/verification/semantic.py       provider-independent semantic verification
src/spanvouch/verification/verdicts.py       verifier aggregation
src/spanvouch/review/protocols.py            repository/workflow/reviser ports
src/spanvouch/review/transitions.py          pure review routing and transition rules
src/spanvouch/review/application.py          create/get/resume/decide use cases
src/spanvouch/adapters/models/deepseek.py     DeepSeek config and HTTP provider
src/spanvouch/adapters/frameworks/langgraph_review.py LangGraph runner
src/spanvouch/adapters/storage/sqlite.py      SQLite schema/repository/CAS/lease implementation
src/spanvouch/labs/supportlab/                SupportLab only
src/spanvouch/evaluation/                     dataset/evaluator entry implementations
src/spanvouch/cli/main.py                     single `spanvouch` command tree
```

---

### Task 1: Integrate Phase 2/3 and freeze the accepted baseline

**Files:**
- Create: `docs/evaluation/phase3-frozen-baseline.md`
- Modify through merge: `docs/superpowers/specs/2026-07-18-phase4-research-foundation-design.md`
- Modify through merge: `docs/handoffs/2026-07-18-phase4-research-foundation-handoff.md`
- Verify: `docs/evaluation/phase3-verification-review.md`
- Create: `docs/evaluation/phase3-reproduction-runbook.md`

**Interfaces:**
- Consumes: `main@dddc7b8b49db81292d72cbac444ad039d17f5dde`, Phase 2 at `4df0ccb847cfee610ada9913e7ba31eec7667fc8`, Phase 3 at `31ff910c72c720fa4a61b52b2687edc2053071e3`, code-under-test `66e8f5d36f7d46db50f7bd962a036fcc94affbe6`, design commit `9407187`, and the commit that contains this plan.
- Produces: an integrated `main`, annotated tag `phase3-frozen-20260718`, and branch `feature/phase4-research-foundation`.

- [ ] **Step 1: Verify exact refs, clean worktrees, and ancestry**

Run from `D:\self agent`:

```powershell
git status --short --branch
git worktree list --porcelain
git rev-parse main
git rev-parse feature/phase2-diagnosis-mvp
git rev-parse feature/phase3-verification-review
git rev-parse docs/ivad-program-design
git merge-base --is-ancestor main feature/phase2-diagnosis-mvp
git merge-base --is-ancestor feature/phase2-diagnosis-mvp feature/phase3-verification-review
git merge-base --is-ancestor 9407187 docs/ivad-program-design
git ls-tree -r --name-only docs/ivad-program-design -- docs/superpowers/plans/2026-07-18-phase4-research-foundation.md
```

Expected: every worktree is clean; main/Phase 2/Phase 3 match the exact SHAs above; all three ancestry commands exit `0`; the final command prints this plan path. Record the current documentation-branch HEAD in the integration report because it necessarily includes commits after `9407187`. Stop if a protected ref differs until the delta is explained in the handoff.

- [ ] **Step 2: Create an isolated integration worktree**

Use the `using-git-worktrees` skill, then run the equivalent of:

```powershell
git worktree add "D:\self agent\.worktrees\phase4-integration" main
```

Expected: the new worktree is on `main` and has no untracked or modified files.

- [ ] **Step 3: Merge Phase 2, Phase 3, and the design branch in order**

```powershell
git merge --no-ff feature/phase2-diagnosis-mvp -m "merge: integrate Phase 2 diagnosis MVP"
git merge --no-ff feature/phase3-verification-review -m "merge: integrate Phase 3 verification review"
git merge --no-ff docs/ivad-program-design -m "merge: integrate IVAD and Phase 4 design"
```

Expected: three merge commits preserve both feature histories. For conflicts, retain Phase 3 source/evaluation behavior and the newest design/research documents; never resolve by deleting either history.

- [ ] **Step 4: Reproduce the accepted Phase 3 offline gate**

```powershell
uv sync --frozen --group dev
uv run ruff check src tests
uv run mypy
.\.venv\Scripts\python.exe -m pytest --cov=afc --cov-report=term-missing
```

Run the exact dataset and deterministic evaluation commands recorded in `docs/evaluation/phase3-verification-review.md`. Run the SQLite-process command and the Docker/restart/non-root/persistence procedure from `docs/evaluation/phase3-reproduction-runbook.md`. Do not edit the historical Phase 3 report; its byte identity is part of this gate.

Expected: 710 tests pass, total coverage is 93%, Ruff/mypy are clean, review metrics remain `1.0/1.0/1.0/0.0`, repeated reports are byte-exact, provider usage is zero, and all Docker/recovery gates pass. The pre-existing Starlette/httpx deprecation warning is allowed and must be recorded.

- [ ] **Step 5: Write the frozen-baseline record**

Create `docs/evaluation/phase3-frozen-baseline.md` with this exact structure and replace only `<integrated-main-sha>` with the verified SHA:

```markdown
# Phase 3 Frozen Baseline

- Integrated main: `<integrated-main-sha>`
- Phase 2 head: `4df0ccb847cfee610ada9913e7ba31eec7667fc8`
- Phase 3 documentation head: `31ff910c72c720fa4a61b52b2687edc2053071e3`
- Code under final acceptance: `66e8f5d36f7d46db50f7bd962a036fcc94affbe6`
- Deterministic review report SHA-256: `ff6af27b596a65d67fe2bda432f296d40e3f4c14a8537975e85ed9a7820fd39e`
- SupportLab manifest SHA-256: `b14eac192e7b683fb908f2f7f54efccb31ab100bf19563476b824d192060cb38`
- Review manifest SHA-256: `677e0075f5b4149db73538411376bf994caa5ba0fdb8ff29b33b487a5fe02076`
- Review candidates SHA-256: `ee04d8d0f1e608fd81c202fca39eeb799f764b3099cfb03d7d94a4ab7eb73bd2`
- Review labels SHA-256: `d41a87247456264863d70f807256a5d1b6f24ab84422dc406a92ef867e36b305`
- Acceptance mode: offline, zero provider calls

This marker freezes behavior and provenance. It does not rename or rewrite historical AFC artifacts.
```

- [ ] **Step 6: Commit, tag, and create the Phase 4 branch**

```powershell
git add docs/evaluation/phase3-frozen-baseline.md
git commit -m "docs: freeze accepted Phase 3 baseline"
git tag -a phase3-frozen-20260718 -m "Phase 3 accepted offline baseline"
git switch -c feature/phase4-research-foundation
```

Expected: `git rev-parse phase3-frozen-20260718^{commit}` identifies the baseline commit, and the new branch starts at that commit. Do not push or rename the remote repository without user approval.

---

### Task 2: Perform the Python distribution and import namespace cutover

**Files:**
- Move: `src/afc/` -> `src/spanvouch/`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `Dockerfile`
- Modify: all `tests/**/*.py`
- Create: `tests/test_package_identity.py`

**Interfaces:**
- Consumes: the integrated Phase 3 package and test suite from Task 1.
- Produces: distribution `spanvouch==0.2.0`, import root `spanvouch`, no importable `afc` package, and otherwise unchanged Python behavior.

- [ ] **Step 1: Add a failing package-identity test**

Create `tests/test_package_identity.py`:

```python
from __future__ import annotations

import importlib.util
import subprocess
import sys

import spanvouch


def test_spanvouch_is_the_only_public_import_root() -> None:
    assert spanvouch.__name__ == "spanvouch"
    assert importlib.util.find_spec("afc") is None


def test_clean_interpreter_cannot_import_afc() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import afc"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "No module named 'afc'" in completed.stderr
```

- [ ] **Step 2: Run the test to prove the cutover is not yet implemented**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_package_identity.py -v
```

Expected: collection fails because `spanvouch` does not exist.

- [ ] **Step 3: Move the package and update every Python import mechanically**

```powershell
git mv src/afc src/spanvouch
rg -l "\bafc\b" src tests pyproject.toml Dockerfile
```

Use one generated `apply_patch` to change Python module references from `afc` to `spanvouch` in `src/**/*.py`, `tests/**/*.py`, `pyproject.toml`, and the Dockerfile command. Do not replace prose, frozen JSON/JSONL, old reports, or historical documents in this task.

The resulting package metadata must contain:

```toml
[project]
name = "spanvouch"
version = "0.2.0"

[tool.hatch.build.targets.wheel]
packages = ["src/spanvouch"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["spanvouch"]
```

The Docker command must import the new app:

```dockerfile
CMD ["/opt/venv/bin/uvicorn", "spanvouch.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 4: Regenerate only package-lock metadata**

```powershell
uv lock
uv sync --frozen --group dev
```

Expected: the lock changes only for the local project name/version, not unrelated dependency upgrades.

- [ ] **Step 5: Verify the package cutover and all Python tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_package_identity.py -v
uv run ruff check src tests
uv run mypy
.\.venv\Scripts\python.exe -m pytest --cov=spanvouch --cov-report=term-missing
```

Expected: package-identity tests pass; all tests pass; coverage is at least 93%. Failures caused only by still-intentional public CLI/config names are deferred to Task 3; no import failure is deferred.

- [ ] **Step 6: Prove old frozen artifacts did not change**

```powershell
git diff phase3-frozen-20260718 -- evals/datasets docs/evaluation/phase3-verification-review.md
```

Expected: no diff in frozen dataset bytes or the Phase 3 acceptance report.

- [ ] **Step 7: Commit the namespace cutover**

```powershell
git add src tests pyproject.toml uv.lock Dockerfile
git commit -m "refactor: cut over Python package to SpanVouch"
```

---

### Task 3: Cut over CLI, environment, container, and public product identity

**Files:**
- Create: `src/spanvouch/cli/main.py`
- Modify: `src/spanvouch/cli/review.py`
- Modify: `src/spanvouch/evals/generate_dataset.py`
- Modify: `src/spanvouch/evals/generate_review_dataset.py`
- Modify: `src/spanvouch/evals/run_diagnosis_eval.py`
- Modify: `src/spanvouch/evals/run_review_eval.py`
- Modify: `src/spanvouch/api/app.py`
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `compose.yaml`
- Modify: `README.md`
- Modify: `tests/test_delivery_config.py`
- Modify: `tests/cli/test_review.py`
- Create: `tests/cli/test_main.py`

**Interfaces:**
- Consumes: existing evaluator/review `main(argv)` functions and `create_app()`.
- Produces: one `spanvouch` executable with `dataset generate`, `dataset generate-review`, `evaluate diagnosis`, `evaluate review`, and `review create|show|resume|decide`; environment variables `SPANVOUCH_DB_PATH` and `SPANVOUCH_API_URL` only.

- [ ] **Step 1: Add failing unified-CLI tests**

Create `tests/cli/test_main.py`:

```python
from __future__ import annotations

from collections.abc import Sequence

import pytest

from spanvouch.cli import main as cli_main


@pytest.mark.parametrize(
    ("argv", "handler_name", "forwarded"),
    [
        (("dataset", "generate", "--output", "x"), "generate_dataset", ("--output", "x")),
        (("dataset", "generate-review", "--output", "x"), "generate_review", ("--output", "x")),
        (("evaluate", "diagnosis", "--output", "x"), "evaluate_diagnosis", ("--output", "x")),
        (("evaluate", "review", "--output", "x"), "evaluate_review", ("--output", "x")),
        (("review", "show", "--case-id", "c1"), "review", ("show", "--case-id", "c1")),
    ],
)
def test_main_routes_to_one_public_command_tree(
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
    handler_name: str,
    forwarded: tuple[str, ...],
) -> None:
    calls: list[tuple[str, Sequence[str]]] = []

    def record(name: str):
        def handler(args: Sequence[str] | None = None) -> int:
            calls.append((name, tuple(args or ())))
            return 0
        return handler

    monkeypatch.setattr(cli_main, handler_name, record(handler_name))
    assert cli_main.main(list(argv)) == 0
    assert calls == [(handler_name, forwarded)]
```

Add to `tests/test_delivery_config.py` assertions that `AFC_DB_PATH`, `AFC_API_URL`, `afc_data`, and every `afc-*` console script are absent from active config, while historical docs/evals are excluded from this scan.

- [ ] **Step 2: Run focused tests and observe failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cli/test_main.py tests/test_delivery_config.py -v
```

Expected: failure because `spanvouch.cli.main` and new public identifiers do not exist.

- [ ] **Step 3: Implement the single CLI router**

Create `src/spanvouch/cli/main.py`:

```python
from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

from spanvouch.cli.review import main as review
from spanvouch.evals.generate_dataset import main as generate_dataset
from spanvouch.evals.generate_review_dataset import main as generate_review
from spanvouch.evals.run_diagnosis_eval import main as evaluate_diagnosis
from spanvouch.evals.run_review_eval import main as evaluate_review

Handler = Callable[[Sequence[str] | None], int]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spanvouch")
    parser.add_argument("command", choices=("dataset", "evaluate", "review"))
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    rest = tuple(arguments.rest)
    if arguments.command == "review":
        return review(rest)
    if not rest:
        _parser().error(f"{arguments.command} requires a subcommand")
    subcommand, forwarded = rest[0], rest[1:]
    handlers: dict[tuple[str, str], Handler] = {
        ("dataset", "generate"): generate_dataset,
        ("dataset", "generate-review"): generate_review,
        ("evaluate", "diagnosis"): evaluate_diagnosis,
        ("evaluate", "review"): evaluate_review,
    }
    handler = handlers.get((arguments.command, subcommand))
    if handler is None:
        _parser().error(f"unknown {arguments.command} subcommand: {subcommand}")
    return handler(forwarded)
```

Refactor `generate_dataset.main` to accept `Sequence[str] | None` and return `int`, matching the other entrypoints. Do not duplicate evaluator logic in the router.

- [ ] **Step 4: Replace console-script metadata**

`pyproject.toml` must contain exactly:

```toml
[project.scripts]
spanvouch = "spanvouch.cli.main:main"
```

Remove all `afc-*` scripts. Update CLI error prefixes to `spanvouch review:`.

- [ ] **Step 5: Replace active environment and delivery names**

Use these exact names:

```text
SPANVOUCH_DB_PATH=.data/spanvouch.db
SPANVOUCH_API_URL=http://127.0.0.1:8000
Compose volume: spanvouch_data
Container database: /data/spanvouch.db
FastAPI title: SpanVouch
FastAPI version: 0.2.0
```

`create_app()` reads only `SPANVOUCH_DB_PATH`; CLI reads only `SPANVOUCH_API_URL`. If only an old `AFC_*` value is present, the program must use the new default rather than silently accepting it.

- [ ] **Step 6: Update README commands without rewriting history**

README examples must use:

```powershell
uv run spanvouch dataset generate --output .cache/readme-check --seed 20260715
uv run spanvouch evaluate diagnosis --output evals/reports/generated/rules.json
uv run spanvouch dataset generate-review --output .cache/review-check --seed 20260717
uv run spanvouch evaluate review --output evals/reports/generated/review-rules.json
uv run spanvouch review show --case-id <case-id>
```

README first screen must say SpanVouch is the system and IVAD is the research method. Do not edit Phase 1–3 historical documents or frozen artifact content.

- [ ] **Step 7: Run CLI/config/API tests and installation smoke**

```powershell
uv lock
uv sync --frozen --group dev
.\.venv\Scripts\python.exe -m pytest tests/cli tests/api tests/test_delivery_config.py -v
uv build
uv run --isolated --with .\dist\spanvouch-0.2.0-py3-none-any.whl spanvouch --help
```

Expected: all focused tests pass; the wheel exposes only `spanvouch`; help lists `dataset`, `evaluate`, and `review`.

- [ ] **Step 8: Run rename inventory and commit**

```powershell
rg -n "AFC_|afc-|src/afc|from afc|import afc|Agent Failure Clinic" src tests pyproject.toml Dockerfile compose.yaml .env.example README.md
```

Expected: zero active hits. Allowed historical hits are checked separately under `docs/` and `evals/` and must not be altered.

```powershell
git add src tests pyproject.toml uv.lock Dockerfile compose.yaml .env.example README.md
git commit -m "refactor: complete SpanVouch public cutover"
```

---

### Task 4: Add the canonical contract/versioning kernel

**Files:**
- Create: `src/spanvouch/contracts/__init__.py`
- Create: `src/spanvouch/contracts/versioning.py`
- Create: `tests/contracts/__init__.py`
- Create: `tests/contracts/test_versioning.py`

**Interfaces:**
- Consumes: Pydantic models and JSON-compatible values.
- Produces: `ContractModel`, `ContractRoot`, `ContractError`, `UnknownSchemaError`, `UnsupportedSchemaVersionError`, `ContractIntegrityError`, `canonical_bytes()`, `canonical_json()`, `canonical_sha256()`, and `require_schema()`.

- [ ] **Step 1: Write failing canonicalization and version tests**

Create `tests/contracts/test_versioning.py`:

```python
from datetime import UTC, datetime
from typing import Literal

import pytest

from spanvouch.contracts.versioning import (
    ContractIntegrityError,
    ContractRoot,
    UnsupportedSchemaVersionError,
    canonical_bytes,
    canonical_sha256,
    require_schema,
)


class ExampleContract(ContractRoot):
    schema_name: Literal["spanvouch.example"] = "spanvouch.example"
    schema_version: Literal["1.0"] = "1.0"
    happened_at: datetime
    value: str


def test_canonical_bytes_are_utf8_sorted_compact_and_utc_z() -> None:
    model = ExampleContract(
        happened_at=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
        value="证据",
    )
    assert canonical_bytes(model) == (
        b'{"happened_at":"2026-07-18T12:00:00Z",'
        b'"schema_name":"spanvouch.example",'
        b'"schema_version":"1.0","value":"\xe8\xaf\x81\xe6\x8d\xae"}'
    )
    assert len(canonical_sha256(model)) == 64


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="extra inputs are not permitted"):
        ExampleContract.model_validate(
            {
                "schema_name": "spanvouch.example",
                "schema_version": "1.0",
                "happened_at": "2026-07-18T12:00:00Z",
                "value": "x",
                "unknown": True,
            }
        )


def test_unknown_major_is_rejected_without_coercion() -> None:
    with pytest.raises(UnsupportedSchemaVersionError):
        require_schema("spanvouch.example", "2.0", supported={"spanvouch.example": {"1.0"}})


def test_hash_mismatch_is_typed() -> None:
    with pytest.raises(ContractIntegrityError):
        canonical_sha256({"a": 1}, expected_sha256="0" * 64)
```

- [ ] **Step 2: Run the tests to verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contracts/test_versioning.py -v
```

Expected: import failure because `spanvouch.contracts.versioning` does not exist.

- [ ] **Step 3: Implement strict roots and typed compatibility errors**

Create `src/spanvouch/contracts/versioning.py` with these public definitions:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue


class ContractError(ValueError):
    pass


class UnknownSchemaError(ContractError):
    pass


class UnsupportedSchemaVersionError(ContractError):
    pass


class ContractIntegrityError(ContractError):
    pass


class ContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ContractRoot(ContractModel):
    schema_name: str
    schema_version: str


def _canonical_value(value: Any) -> JsonValue:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python"))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ContractError("canonical timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        raise ContractError("NaN and Infinity are not canonical JSON")
    return value


def canonical_bytes(value: BaseModel | JsonValue) -> bytes:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json(value: BaseModel | JsonValue) -> str:
    return canonical_bytes(value).decode("utf-8")


def canonical_sha256(
    value: BaseModel | JsonValue,
    *,
    expected_sha256: str | None = None,
) -> str:
    digest = sha256(canonical_bytes(value)).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ContractIntegrityError("canonical SHA-256 mismatch")
    return digest


def require_schema(
    schema_name: str,
    schema_version: str,
    *,
    supported: dict[str, set[str]],
) -> None:
    versions = supported.get(schema_name)
    if versions is None:
        raise UnknownSchemaError(f"unknown schema: {schema_name}")
    if schema_version not in versions:
        raise UnsupportedSchemaVersionError(
            f"unsupported schema version: {schema_name}/{schema_version}"
        )
```

Re-export the stable public names from `contracts/__init__.py`.

- [ ] **Step 4: Replace duplicate canonical helpers without moving domain models yet**

Change `diagnosis/evidence.py` and `review/models.py` to import `canonical_json` and `canonical_sha256` from `contracts.versioning`. Keep temporary re-exports in those modules only until Tasks 5–8 update all callers; do not keep two implementations.

- [ ] **Step 5: Run focused and full regression tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contracts/test_versioning.py tests/diagnosis/test_evidence.py tests/review/test_models.py tests/review/test_policy_identity.py -v
uv run ruff check src tests
uv run mypy
```

Expected: all tests pass and identical existing inputs still hash to their Phase 3 values, except new ContractRoot fixtures which include schema metadata.

- [ ] **Step 6: Commit the canonical kernel**

```powershell
git add src/spanvouch/contracts src/spanvouch/diagnosis/evidence.py src/spanvouch/review/models.py tests/contracts tests/diagnosis/test_evidence.py tests/review
git commit -m "feat: add canonical contract versioning kernel"
```

---

## Final pre-execution review

Before dispatching Task 1, the controller must confirm:

- [ ] The user has approved the design and this implementation plan.
- [ ] Tasks will execute numerically from 1 through 16 despite physical section grouping.
- [ ] The Task 1 documentation-branch HEAD contains design commit `9407187` and this plan.
- [ ] Every implementation Task ends in a focused test gate and a task-sized commit.
- [ ] No Task silently changes Phase 3 verifier policy, revision bound, persistence, recovery, security, or evaluator formulas.
- [ ] New Phase 4 report bytes are compared run-to-run; old Phase 3 artifact hashes are checked independently and never rewritten.
- [ ] Contract names, versions, model fields, and Interface signatures are consistent across Tasks 4–15.
- [ ] Phase 4 makes no claim that independent semantic verification, Conformal risk control, layered acquisition, or OOD generalization is already effective.
- [ ] Remote rename, push, publication, and release remain outside execution authority until the user separately approves them.

Execution recommendation: **Subagent-Driven Development**. Use one fresh implementation worker per Task, then run requirements-conformance review and code-quality review before accepting the commit and starting the next Task. The current design thread does not execute implementation.
