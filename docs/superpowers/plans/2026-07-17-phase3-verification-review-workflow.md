# Phase 3 Verification Review Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an auditable diagnosis-review workflow that independently verifies Phase 2 reports, permits at most one evidence-focused revision, persists every boundary in SQLite, and requires a final human confirm/correct/reject decision through both API and CLI.

**Architecture:** Keep Phase 2 diagnosis unchanged as the producer. A sanitized `DiagnosticTraceView` snapshot and immutable diagnosis revisions enter a layered review domain: deterministic `EvidenceVerifier`, optional independent DeepSeek `SemanticVerifier`, a pure verdict merger, and a bounded LangGraph coordinator. `ReviewRepository` is the persistence boundary; its SQLite implementation is the source of truth for state, leases, idempotency, append-only history, and recovery. FastAPI and the HTTP-only CLI are adapters over `ReviewService`, never alternate workflow engines.

**Tech Stack:** Python 3.12.13, Pydantic 2, FastAPI, LangGraph, standard-library `sqlite3`, httpx, DeepSeek's OpenAI-compatible API adapter, pytest/pytest-asyncio, Ruff, mypy, Docker Compose, and the existing TraceIR/SupportLab/diagnosis modules.

## Global Constraints

- Do not repeat or modify Phase 1 or Phase 2 implementation work except for the narrow extension seams named in this plan.
- Do not start implementation from stale local `main`. Recheck PR #1 and the remote SHAs before Task 1.
- Keep Phase 2's `Diagnoser.diagnose(view, evidence)` contract backward compatible. Add revision as an optional protocol; `RuleDiagnoser` must remain non-revision-capable.
- `DiagnosticTraceView` and `EvidenceCatalog` remain the only trace data visible to diagnosers and verifiers. Never persist raw `TraceIR` in the review database.
- Never provide gold labels, mutation labels, deterministic findings, diagnosis prompts, raw provider bodies, hidden reasoning, semantic run IDs, `trace_id`, or `run_id` to `SemanticVerifier`.
- The default API, CLI, test suite, evaluator, and container smoke path are `rules + deterministic` and must make zero external model calls.
- DeepSeek diagnosis or hybrid verification must require explicit opt-in. Read credentials only from `DEEPSEEK_API_KEY`; never write a key to SQLite, logs, events, fixtures, reports, exceptions, or CLI output.
- Every review case must reach human review after verification. A verifier never grants release authority.
- Evidence revision count is permanently capped at one. A second `needs_evidence` result routes to human review without another revision.
- Human correction accepts selectors and semantic diagnosis fields only. The server must rebuild evidence IDs, observed values, hashes, and provenance from the stored snapshot.
- SQLite is authoritative. LangGraph coordinates one invocation but owns no durable state and uses no in-memory checkpoint as a recovery mechanism.
- Use compare-and-swap for state/version changes and a persisted lease for recoverable work. Model calls may be at-least-once after a crash, but revisions, verifier runs, events, and decisions must not be duplicated.
- Preserve all Phase 1/2 frozen dataset bytes, existing API behavior, deterministic report formatting, Docker non-root UID/GID `10001:10001`, and current CI gates.
- Use strict RED -> GREEN -> REFACTOR for each behavior. Run the focused test before implementation and again after implementation.
- One task equals one reviewable commit. Do not mix later tasks into an earlier commit.

---

## Verified Planning Baseline

Verified on 2026-07-17 before writing this plan:

```text
working tree: D:\self agent\.worktrees\phase2-diagnosis-mvp
planning branch: docs/phase3-verification-review-design
planning base HEAD: 06d90ab (docs: hand off Phase 3 design to planning)
Phase 3 design commit: 894cc7e
Phase 2 branch and PR #1 head: 4df0ccb
origin/main: fbec526
local main: dddc7b8 (stale; do not use as implementation base)
PR #1 state at planning time: OPEN, not merged
```

Before Task 1, run:

```powershell
git status --short --branch
git ls-remote origin refs/heads/main refs/heads/feature/phase2-diagnosis-mvp
gh pr view 1 --json state,mergedAt,baseRefOid,headRefOid
```

- If PR #1 is still open, create `feature/phase3-verification-review` from the commit containing this plan so the Phase 3 work remains explicitly stacked on Phase 2.
- If PR #1 has merged, fetch `origin`, reconcile this documentation branch with the new `origin/main`, and only then create the isolated Phase 3 implementation worktree.
- In either case, record the chosen base SHA in the first implementation commit message or execution log. Never branch from local `main` until it matches `origin/main`.

---

## File Map

```text
src/afc/review/models.py                         immutable review domain models
src/afc/review/protocols.py                      verifier and repository boundaries
src/afc/review/verdicts.py                       pure transition and merge policies
src/afc/review/evidence_verifier.py              deterministic grounding verifier
src/afc/review/semantic_verifier.py              independent DeepSeek verifier
src/afc/review/commands.py                       transactional repository commands
src/afc/review/schema.py                         SQLite schema v2 and connection policy
src/afc/review/sqlite_repository.py              transactional persistence adapter
src/afc/review/service.py                        application use cases and human decisions
src/afc/review/workflow.py                       bounded LangGraph coordinator
src/afc/review/reviser.py                        optional diagnoser-revision adapter
src/afc/api/routes/diagnosis_reviews.py          review HTTP endpoints and stable errors
src/afc/cli/review.py                            HTTP-only afc-review command
src/afc/evals/generate_review_dataset.py         deterministic 36-candidate generator
src/afc/evals/review_labels.py                   review label and manifest validation
src/afc/evals/review_metrics.py                  deterministic verification metrics
src/afc/evals/run_review_eval.py                 offline/live review evaluator
evals/datasets/supportlab-review-v1/              frozen reports, labels, and manifest
docs/evaluation/phase3-verification-review.md     evidence and operational results
tests/review/                                     domain, repository, and workflow tests
tests/api/test_diagnosis_reviews.py               API contract tests
tests/cli/test_review.py                          CLI contract tests
tests/evals/test_review_*.py                      fixture and evaluator tests
```

## Task 1: Review Domain Models, Hashes, and Pure Policies

**Files:**

- Create: `src/afc/review/__init__.py`
- Create: `src/afc/review/models.py`
- Create: `src/afc/review/verdicts.py`
- Create: `tests/review/__init__.py`
- Create: `tests/review/factories.py`
- Create: `tests/review/test_models.py`
- Create: `tests/review/test_verdicts.py`

**Interfaces:**

- `ReviewStatus`, `VerificationMode`, `VerifierKind`, `VerifierVerdict`, `FindingSeverity`, `FindingCode`, `DecisionAction`, and `RevisionOrigin` are string enums.
- `VerificationFinding`, `EvidenceGap`, `VerifierProvenance`, `VerifierReport`, `DiagnosisRevision`, `ReviewInputSnapshot`, `VerificationInput`, `DiagnosisCorrectionDraft`, `HumanDecisionDraft`, `HumanReviewDecision`, `DiagnosisReviewCase`, `DiagnosisReviewDetail`, and `ReviewRuntimeBundle` are frozen Pydantic models with `extra="forbid"`.
- `canonical_sha256(model_or_json)` hashes UTF-8 canonical JSON with sorted keys and compact separators.
- `assert_transition(current, target)` enforces the state graph; `merge_verifier_reports(deterministic, semantic)` is a pure truth table.

- [ ] **Step 1: Write failing model-invariant and hash tests**

Create tests that prove:

```python
def test_snapshot_hash_is_stable_for_canonical_json() -> None:
    first = make_review_snapshot()
    second = first.model_copy(update={"view_json": first.view_json})
    assert first.input_sha256 == second.input_sha256


def test_revision_zero_has_no_previous_hash() -> None:
    revision = make_revision(revision_number=0, previous_report_sha256=None)
    assert revision.report_sha256 == canonical_sha256(revision.report)


def test_revision_one_requires_previous_hash_and_gap_ids() -> None:
    with pytest.raises(ValidationError):
        make_revision(
            revision_number=1,
            previous_report_sha256=None,
            triggering_gap_ids=(),
        )


def test_correction_draft_rejects_forged_observed_value() -> None:
    payload = make_correction_draft().model_dump(mode="json")
    payload["observed_value"] = "forged"
    with pytest.raises(ValidationError):
        DiagnosisCorrectionDraft.model_validate(payload)
```

The shared factory must build one valid `DiagnosticTraceView`, catalog-backed `DiagnosisReport`, snapshot, verifier report, revision, and awaiting-human case without reading files or labels.

- [ ] **Step 2: Run RED for models**

Run: `.venv\Scripts\python -m pytest tests/review/test_models.py -q`

Expected: FAIL because `afc.review.models` does not exist.

- [ ] **Step 3: Implement the exact model vocabulary**

Use these enum values:

```python
class ReviewStatus(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    VERIFYING = "verifying"
    REVISION_REQUESTED = "revision_requested"
    REVISING = "revising"
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    REJECTED = "rejected"


class FindingCode(StrEnum):
    INVALID_SELECTOR = "invalid_selector"
    EVIDENCE_VALUE_MISMATCH = "evidence_value_mismatch"
    EVIDENCE_HASH_MISMATCH = "evidence_hash_mismatch"
    CLAIM_NOT_GROUNDED = "claim_not_grounded"
    CRITICAL_SPAN_NOT_GROUNDED = "critical_span_not_grounded"
    DUPLICATE_REFERENCE = "duplicate_reference"
    EVIDENCE_BUDGET_EXCEEDED = "evidence_budget_exceeded"
    CLEAN_TRACE_CONFLICT = "clean_trace_conflict"
    UNSUPPORTED_SCOPE = "unsupported_scope"
    DIAGNOSIS_CONFLICT = "diagnosis_conflict"
    ALTERNATIVE_HYPOTHESIS = "alternative_hypothesis"
    SEMANTIC_SUPPORT_MISSING = "semantic_support_missing"
    INVALID_VERIFIER_OUTPUT = "invalid_verifier_output"
    PROVIDER_OPERATIONAL_ERROR = "provider_operational_error"
```

The remaining enums are exact and closed:

```text
VerificationMode: deterministic, hybrid
VerifierKind: deterministic, semantic
VerifierVerdict: verified, needs_evidence, review_required
FindingSeverity: hard, advisory, operational
DecisionAction: confirm, correct, reject
RevisionOrigin: initial_diagnosis, evidence_revision, human_correction
```

Define findings with a stable `finding_id`, `code`, `severity`, `message`, `revisable`, and sorted tuples of related selectors/span IDs. Define gaps with `gap_id`, `finding_code`, optional `claim_index`/`stage`, `required_evidence_kind`, `allowed_selectors`, `related_span_ids`, and one bounded instruction. `VerifierReport` records provenance/usage/operational metadata and validates that `verified` has no hard finding or gap. The five-finding/three-gap limit applies only to the private Semantic Verifier draft in Task 9, not to deterministic reports.

`ReviewInputSnapshot` stores only `trace_id`, `run_id`, canonical `DiagnosticTraceView` JSON, `input_sha256`, `catalog_version`, and creation time. `VerificationInput` binds one snapshot, one complete report, and the expected 64-character `report_sha256`; the deterministic verifier recomputes that hash before all other policy checks. `HumanDecisionDraft` is the caller-supplied action/version/reviewer/reason/correction; `HumanReviewDecision` adds the server-generated decision ID, timestamps, and resulting revision reference. `ReviewRuntimeBundle` includes the private snapshot for workflow execution, while the public aggregate may expose trace/run binding but must not expose `view_json`.

- [ ] **Step 4: Run GREEN for models**

Run: `.venv\Scripts\python -m pytest tests/review/test_models.py -q`

Expected: PASS.

- [ ] **Step 5: Write failing transition and verdict truth-table tests**

Cover every allowed edge and explicit rejection of terminal, backward, and second-revision transitions. Cover these merge rows:

```text
deterministic hard and revisable             -> needs_evidence
deterministic hard and not revisable         -> review_required
deterministic verified, no semantic report   -> verified
deterministic verified + semantic verified   -> verified
deterministic verified + semantic needs      -> needs_evidence
deterministic verified + semantic review     -> review_required
deterministic/semantic disagreement           -> review_required
semantic provider operational error          -> review_required
```

- [ ] **Step 6: Run RED for policies**

Run: `.venv\Scripts\python -m pytest tests/review/test_verdicts.py -q`

Expected: FAIL because transition and merge functions are absent.

- [ ] **Step 7: Implement pure policies and stable ordering**

`merge_verifier_reports` must preserve verifier reports unchanged, derive only the composite verdict, and order merged findings by verifier kind, severity, code, then finding ID. It must never mutate a deterministic report based on semantic output.

- [ ] **Step 8: Run GREEN and static checks**

Run:

```powershell
.venv\Scripts\python -m pytest tests/review/test_models.py tests/review/test_verdicts.py -q
.venv\Scripts\python -m ruff check src/afc/review/models.py src/afc/review/verdicts.py tests/review
.venv\Scripts\python -m mypy
```

Expected: all commands PASS.

- [ ] **Step 9: Commit Task 1**

```powershell
git add src/afc/review tests/review
git commit -m "feat: define diagnosis review domain"
```

## Task 2: EvidenceVerifier Identity, Selector, and Grounding Checks

**Files:**

- Create: `src/afc/review/protocols.py`
- Create: `src/afc/review/evidence_verifier.py`
- Create: `tests/review/test_evidence_verifier.py`

**Interfaces:**

```python
class Verifier(Protocol):
    kind: VerifierKind
    version_fingerprint: str

    async def verify(self, input_: VerificationInput) -> VerifierReport:
        raise NotImplementedError


class EvidenceVerifier:
    kind = VerifierKind.DETERMINISTIC

    def __init__(self, engine: InvariantEngine, *, policy_version: str) -> None:
        self._engine = engine
        self._policy_version = policy_version

    async def verify(self, input_: VerificationInput) -> VerifierReport:
        raise NotImplementedError
```

- [ ] **Step 1: Write failing table-driven integrity tests**

Starting from the shared valid report, mutate exactly one field per case and assert the exact finding code for:

- report `trace_id` or `run_id` not matching the snapshot;
- stale report fingerprint in a `DiagnosisRevision`;
- incomplete provenance version fields;
- duplicate critical span, evidence ID, selector, or claim evidence reference;
- unknown selector;
- selector resolving to a different observed value;
- selector resolving to a different SHA-256;
- claim referencing an unknown evidence ID;
- diagnosed claim with no evidence;
- critical span with no same-span evidence.

Add a control asserting the valid report returns `verified`, empty findings, and empty gaps.

For impossible-through-Pydantic states such as duplicate evidence IDs or a claim that references no known evidence, use `model_construct` only in these defense-in-depth unit tests. Normal application and repository paths must continue to use full validation.

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python -m pytest tests/review/test_evidence_verifier.py -q`

Expected: FAIL because `EvidenceVerifier` is not implemented.

- [ ] **Step 3: Implement snapshot decoding and local re-resolution**

The verifier must:

1. Recompute `input_.report` SHA-256 and compare it with `input_.report_sha256`, then parse `input_.snapshot.view_json` back into `DiagnosticTraceView` and recompute `input_sha256` before use.
2. Build `EvidenceCatalog.from_view(view)` locally.
3. Resolve every report selector with `EvidenceSelector` and compare both `observed_value` and `value_sha256` using Phase 2 `canonical_json`.
4. Derive deterministic finding/gap IDs from policy version, code, report hash, and sorted affected selectors; do not use random UUIDs or wall-clock time for content identity.
5. Emit every applicable finding in stable order rather than stopping after the first defect.

Identity mismatch, tampered snapshot/report hash, missing provenance, duplicate references, and forged value/hash are hard non-revisable findings. An otherwise bound report with an invalid selector, claim-grounding defect, or critical-span-grounding defect is hard but revisable and receives selector-constrained gaps.

- [ ] **Step 4: Run GREEN and Phase 2 evidence regression tests**

Run:

```powershell
.venv\Scripts\python -m pytest tests/review/test_evidence_verifier.py tests/diagnosis/test_evidence.py tests/diagnosis/test_diagnosis_models.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 5: Refactor finding construction without changing output**

Extract private pure helpers for finding IDs, gap IDs, selector resolution, and same-span grounding. Keep `EvidenceVerifier.verify` orchestration readable and independent of FastAPI/SQLite.

- [ ] **Step 6: Commit Task 2**

```powershell
git add src/afc/review/protocols.py src/afc/review/evidence_verifier.py tests/review/test_evidence_verifier.py
git commit -m "feat: verify diagnosis evidence integrity"
```

## Task 3: Evidence Budgets, Scope Guards, and Invariant Conflicts

**Files:**

- Modify: `src/afc/review/evidence_verifier.py`
- Create: `tests/review/test_evidence_policy.py`

**Interfaces:**

- Maximum four unique evidence references per claim.
- Maximum eight unique evidence references per report.
- `EvidenceVerifier` reruns the existing SupportLab invariant engine only on the stored `DiagnosticTraceView` projection.

- [ ] **Step 1: Write failing policy tests**

Cover:

- five evidence references on one claim -> `evidence_budget_exceeded` plus a revisable gap;
- nine evidence references in one report -> the same exact code and a report-level gap;
- root outcome `success`/clean report contradicted by a diagnosed failure -> `clean_trace_conflict`;
- unsupported guard hit with anything except `abstained/unsupported_failure_type` -> `unsupported_scope`;
- supported hard invariant type differs from diagnosis type -> `diagnosis_conflict`;
- loop report's first critical span differs from the deterministic last repeated span -> `critical_span_not_grounded`;
- deterministic verifier findings themselves do not count toward evidence budgets;
- no test reads `scenario.*`, diagnosis labels, or review mutation labels.

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python -m pytest tests/review/test_evidence_policy.py -q`

Expected: FAIL on the first missing budget/scope policy.

- [ ] **Step 3: Implement budget gaps and invariant comparison**

Use the existing `InvariantEngine` and `supportlab_rules()` rather than duplicating rule logic. Convert the snapshot projection into the existing `RuleContext` through one private adapter. Keep policy outcomes explicit:

```text
integrity tamper                  hard, non-revisable -> review_required
missing/too much legal evidence  hard, revisable     -> needs_evidence
clean or unsupported conflict    hard, non-revisable -> review_required
supported type conflict          hard, non-revisable -> review_required
loop critical-span mismatch      hard, revisable     -> needs_evidence
```

Allowed selectors in a gap must be a sorted subset of the locally rebuilt catalog and constrained to the affected claim or critical span. Never copy a client-supplied observed value into a gap.

- [ ] **Step 4: Run GREEN and invariant regressions**

Run:

```powershell
.venv\Scripts\python -m pytest tests/review/test_evidence_policy.py tests/invariants tests/diagnosis/test_rule_diagnoser.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 5: Commit Task 3**

```powershell
git add src/afc/review/evidence_verifier.py tests/review/test_evidence_policy.py
git commit -m "feat: enforce diagnosis review policy"
```

## Task 4: Frozen 36-Candidate Review Dataset and Offline Evaluator

**Files:**

- Create: `src/afc/evals/generate_review_dataset.py`
- Create: `src/afc/evals/review_labels.py`
- Create: `src/afc/evals/review_metrics.py`
- Create: `src/afc/evals/run_review_eval.py`
- Modify: `pyproject.toml`
- Create: `tests/evals/test_generate_review_dataset.py`
- Create: `tests/evals/test_review_labels.py`
- Create: `tests/evals/test_review_metrics.py`
- Create: `tests/evals/test_run_review_eval.py`
- Create: `evals/datasets/supportlab-review-v1/review-candidates-v1.jsonl`
- Create: `evals/datasets/supportlab-review-v1/review-labels-v1.jsonl`
- Create: `evals/datasets/supportlab-review-v1/manifest.json`

**Interfaces:**

- `ReviewCandidate` contains `candidate_id`, `source_run_id`, `mutation_kind`, and one complete `DiagnosisReport`.
- `ReviewGoldLabel` contains expected verifier verdict and exact expected finding-code set; labels are evaluator-only.
- `ReviewDatasetManifest` records schema version, 36/20/16 counts, per-file SHA-256, Phase 2 source manifest SHA-256, generator version, and fixed seed.
- `afc-generate-review-dataset` reproduces the frozen directory without external calls.
- `afc-evaluate-review` defaults to deterministic verification and emits canonical one-line JSON.

- [ ] **Step 1: Write failing cohort-shape tests**

Assert the generated cohort is exactly:

```text
20 unmodified valid RuleDiagnoser reports
 2 invalid_selector mutations
 2 evidence_value/hash_mismatch mutations
 2 claim_not_grounded mutations
 2 critical_span_not_grounded mutations
 2 supported diagnosis_conflict mutations
 6 unsupported_scope forced-classification mutations
36 total candidates, 16 total mutations
```

The six unsupported candidates must cover both examples of each Phase 2 unsupported family. Each mutation must start from a frozen Phase 2 trace and alter only the diagnosis candidate, never the source trace, Phase 2 labels, or Phase 2 manifest.

- [ ] **Step 2: Run RED for generation**

Run: `.venv\Scripts\python -m pytest tests/evals/test_generate_review_dataset.py -q`

Expected: FAIL because the review generator does not exist.

- [ ] **Step 3: Implement deterministic generation and freeze the files**

The generator must build valid reports through `DiagnosisService` with `RuleDiagnoser`, then apply named pure mutation functions. Use a fixed ordering by source run ID and mutation kind, LF endings, no timestamps, and canonical compact JSON. Register:

```toml
afc-generate-review-dataset = "afc.evals.generate_review_dataset:main"
afc-evaluate-review = "afc.evals.run_review_eval:main"
```

Generate into a temporary directory first, inspect the manifest, then copy the reviewed artifacts into `evals/datasets/supportlab-review-v1` through the normal implementation workflow. Do not modify any existing `supportlab-v1` file.

- [ ] **Step 4: Write failing label-boundary and manifest tests**

Tests must reject duplicate candidate IDs, unknown source run IDs, label/candidate join drift, invalid expected finding codes, changed file hash, changed Phase 2 source hash, and CRLF output. Add a test that monkeypatches label loading to fail if generation or verification code attempts to import labels at runtime.

- [ ] **Step 5: Run RED for labels**

Run: `.venv\Scripts\python -m pytest tests/evals/test_review_labels.py -q`

Expected: FAIL because strict loading and manifest validation are absent.

- [ ] **Step 6: Implement labels, metrics, and deterministic evaluation**

Return at least:

```python
class ReviewMetrics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    valid_report_pass_rate: float
    hard_defect_recall: float
    false_block_rate: float
    unsupported_scope_detection_rate: float
    claim_grounding_detection_rate: float
    critical_grounding_detection_rate: float
    evidence_gap_precision: float
    structured_output_success_rate: float
    operational_error_rate: float
```

`ReviewEvaluationReport` must include schema version, candidate count, ordered sample results, metrics, verifier version/policy hash, and usage summary. It must exclude gold labels from each emitted verifier report and use exact canonical JSON in `write_report`.

- [ ] **Step 7: Prove RED then GREEN for metrics and byte exactness**

Run before implementation:

```powershell
.venv\Scripts\python -m pytest tests/evals/test_review_metrics.py tests/evals/test_run_review_eval.py -q
```

Expected RED: missing metric/evaluator symbols.

Run after implementation:

```powershell
.venv\Scripts\python -m pytest tests/evals/test_generate_review_dataset.py tests/evals/test_review_labels.py tests/evals/test_review_metrics.py tests/evals/test_run_review_eval.py -q
.venv\Scripts\python -m afc.evals.generate_review_dataset --output .cache/review-dataset-check --seed 20260717
.venv\Scripts\python -c "from pathlib import Path; import hashlib; a=Path('evals/datasets/supportlab-review-v1'); b=Path('.cache/review-dataset-check'); assert all(hashlib.sha256((a/n).read_bytes()).digest()==hashlib.sha256((b/n).read_bytes()).digest() for n in ('review-candidates-v1.jsonl','review-labels-v1.jsonl','manifest.json'))"
.venv\Scripts\python -m afc.evals.run_review_eval --output .cache/review-a.json
.venv\Scripts\python -m afc.evals.run_review_eval --output .cache/review-b.json
.venv\Scripts\python -c "from pathlib import Path; assert Path('.cache/review-a.json').read_bytes()==Path('.cache/review-b.json').read_bytes()"
```

Expected GREEN: 20/20 valid pass, 16/16 injected defects detected, 6/6 unsupported forced classifications blocked, and byte-exact repeated reports.

- [ ] **Step 8: Commit Task 4**

```powershell
git add pyproject.toml src/afc/evals tests/evals evals/datasets/supportlab-review-v1
git commit -m "feat: add deterministic review evaluation"
```

## Task 5: ReviewRepository and SQLite Schema v2

**Files:**

- Create: `src/afc/review/schema.py`
- Create: `src/afc/review/sqlite_repository.py`
- Create: `src/afc/review/commands.py`
- Modify: `src/afc/review/protocols.py`
- Create: `src/afc/review/errors.py`
- Create: `tests/review/test_sqlite_schema.py`
- Create: `tests/review/test_sqlite_repository.py`

**Interfaces:**

`ReviewRepository` exposes use-case-level async operations, not arbitrary SQL:

```python
class ReviewRepository(Protocol):
    async def initialize(self) -> None:
        raise NotImplementedError

    async def create_case(self, command: CreateReviewCase) -> DiagnosisReviewDetail:
        raise NotImplementedError

    async def get_detail(self, case_id: str) -> DiagnosisReviewDetail:
        raise NotImplementedError

    async def load_runtime(self, case_id: str) -> ReviewRuntimeBundle:
        raise NotImplementedError

    async def claim_work(self, command: ClaimReviewWork) -> DiagnosisReviewCase:
        raise NotImplementedError

    async def append_verifier_run(self, command: AppendVerifierRun) -> DiagnosisReviewCase:
        raise NotImplementedError

    async def append_revision(self, command: AppendDiagnosisRevision) -> DiagnosisReviewCase:
        raise NotImplementedError

    async def route_to_human(self, command: RouteToHumanReview) -> DiagnosisReviewCase:
        raise NotImplementedError

    async def apply_human_decision(self, command: ApplyHumanDecision) -> DiagnosisReviewDetail:
        raise NotImplementedError
```

Define the immutable command models in `review/commands.py`. Each command includes `expected_version`, prior/target status, idempotency scope/key/fingerprint where applicable, stable entity IDs, UTC timestamps supplied by the service clock, and event metadata. Repository methods own the complete transaction that changes state and appends history.

- [ ] **Step 1: Write failing schema initialization tests**

Cover fresh creation, repeated initialization, exact schema version `2`, refusal of unknown/newer versions, and explicit rejection of unpublished development schema v1 databases, which must be rebuilt rather than silently migrated. Also cover `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, non-zero `busy_timeout`, and all eight required tables:

```text
schema_metadata
review_cases
review_inputs
diagnosis_revisions
verifier_runs
human_decisions
workflow_events
idempotency_keys
```

- [ ] **Step 2: Run RED for schema**

Run: `.venv\Scripts\python -m pytest tests/review/test_sqlite_schema.py -q`

Expected: FAIL because schema initialization is absent.

- [ ] **Step 3: Implement schema v2 with explicit constraints**

Use `TEXT` for UUIDs, enums, timestamps, canonical JSON, and hashes; `INTEGER` for versions/counts; and foreign keys with no cascade that could erase audit history. Required uniqueness/constraints:

```text
review_inputs.case_id UNIQUE
diagnosis_revisions (case_id, revision_number) UNIQUE
verifier_runs (case_id, verifier_run_id) UNIQUE
human_decisions.case_id UNIQUE
workflow_events (case_id, event_sequence) UNIQUE
idempotency_keys (scope, idempotency_key) UNIQUE
review_cases evidence_revision_count BETWEEN 0 AND 1
all stored SHA-256 values length 64
```

The schema ownership is exact:

```text
schema_metadata: singleton_key, schema_version
review_cases: case_id, status, version, verification_mode, diagnoser,
              current_revision_number, evidence_revision_count,
              deterministic_run_id, semantic_run_id, composite_verdict,
              terminal_decision_id, lease_owner, lease_expires_at,
              created_at, updated_at
review_inputs: case_id, trace_id, run_id, view_json, input_sha256,
               catalog_version, created_at
diagnosis_revisions: revision_id, case_id, revision_number, origin,
                     previous_report_sha256, report_json, report_sha256,
                     triggering_gap_ids_json, provenance_json, created_at
verifier_runs: verifier_run_id, case_id, revision_number, verifier_kind,
               report_json, verdict, usage_json, operational_error_json,
               started_at, completed_at
human_decisions: decision_id, case_id, action, reviewer_label, reason,
                 expected_version, correction_revision_number, created_at
workflow_events: event_id, case_id, event_sequence, event_type,
                 from_status, to_status, case_version, metadata_json, created_at
idempotency_keys: scope, idempotency_key, request_sha256,
                  result_type, result_id, created_at
```

Open a fresh connection inside the worker thread for each transaction, apply connection PRAGMAs on every connection, use `BEGIN IMMEDIATE` for writes, commit explicitly, and roll back on every exception. Public async methods call private synchronous transactions with `asyncio.to_thread`; do not share a `sqlite3.Connection` across event-loop threads.

- [ ] **Step 4: Write failing repository transaction tests**

Cover:

- case creation atomically writes snapshot, revision 0, initial event, and idempotency record;
- forced failure after any insert leaves all related tables unchanged;
- same idempotency key/fingerprint returns the original case;
- same key/different fingerprint raises `ReviewConflictError`;
- immutable revision 0 and append-only revision 1;
- a second revision is rejected;
- CAS version or status mismatch affects zero rows and raises conflict;
- only an expired lease may be reclaimed;
- duplicate verifier result/event IDs do not duplicate rows;
- exactly one terminal human decision wins a two-reviewer race;
- repository close/reopen reconstructs the byte-equivalent aggregate and event ordering.

- [ ] **Step 5: Run RED for repository**

Run: `.venv\Scripts\python -m pytest tests/review/test_sqlite_repository.py -q`

Expected: FAIL on missing transactional operations.

- [ ] **Step 6: Implement row codecs and transactional commands**

All Pydantic payloads must be stored with one shared canonical JSON encoder and validated again on read. Convert `sqlite3.IntegrityError`, `OperationalError`, and zero-row CAS updates into typed review errors without including SQL text, filesystem paths, snapshots, or secrets in error messages.

Persist events for `case_created`, `verification_started`, `verification_completed`, `revision_requested`, `revision_started`, `revision_completed`, `awaiting_human_review`, `human_confirmed`, `human_corrected`, `human_rejected`, and `provider_failed`. Event sequence is assigned inside the transaction as previous max plus one.

- [ ] **Step 7: Run GREEN and concurrency loop**

Run:

```powershell
.venv\Scripts\python -m pytest tests/review/test_sqlite_schema.py tests/review/test_sqlite_repository.py -q
1..20 | ForEach-Object { .venv\Scripts\python -m pytest tests/review/test_sqlite_repository.py -q; if ($LASTEXITCODE -ne 0) { throw "repository repetition failed" } }
```

Expected: every run PASS with no locked-database flake and no orphan row.

- [ ] **Step 8: Commit Task 5**

```powershell
git add src/afc/review/schema.py src/afc/review/sqlite_repository.py src/afc/review/commands.py src/afc/review/protocols.py src/afc/review/errors.py tests/review/test_sqlite_schema.py tests/review/test_sqlite_repository.py
git commit -m "feat: persist diagnosis reviews in sqlite"
```

## Task 6: ReviewService Creation, Idempotency, and Human Decisions

**Files:**

- Create: `src/afc/review/service.py`
- Modify: `src/afc/review/protocols.py`
- Create: `tests/review/test_service.py`

**Interfaces:**

```python
class ReviewService:
    async def create(
        self,
        trace: TraceIR,
        *,
        diagnoser: DiagnoserKind,
        verification_mode: VerificationMode,
        idempotency_key: str,
    ) -> DiagnosisReviewDetail:
        raise NotImplementedError

    async def get(self, case_id: str) -> DiagnosisReviewDetail:
        raise NotImplementedError

    async def resume(self, case_id: str) -> DiagnosisReviewDetail:
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

Add a small `ReviewWorkflowRunner` protocol with `run(case_id)` and `resume(case_id)` methods so the service can be tested before the concrete graph exists. Inject `DiagnosisService`, `ReviewRepository`, that workflow runner, a UUID factory, and an aware UTC clock. Do not call `datetime.now()` or `uuid4()` inside domain/repository code.

- [ ] **Step 1: Write failing create/idempotency tests**

Prove that create:

1. Diagnoses the supplied trace with the requested Phase 2 diagnoser.
2. Builds a canonical `DiagnosticTraceView` snapshot and catalog fingerprint.
3. Persists snapshot + revision 0 before workflow execution.
4. Returns the original aggregate for identical retries.
5. Rejects the same key with a different trace/diagnoser/mode fingerprint.
6. Defaults are supplied by the adapter, not silently changed in the service.

- [ ] **Step 2: Run RED and implement create/get boundary**

Run RED: `.venv\Scripts\python -m pytest tests/review/test_service.py -q`

Expected: FAIL because `ReviewService` does not exist.

Implement create/get, then rerun the same command until the create/idempotency group passes. The workflow dependency may be a recording fake at this stage; production LangGraph wiring belongs to Task 7.

- [ ] **Step 3: Write failing human-decision tests**

Cover:

- confirm on `verified` needs no reason;
- confirm on `needs_evidence`/`review_required` requires a non-empty override reason;
- reject always requires a reason and creates no replacement diagnosis;
- correct accepts only a complete structured draft;
- correction selectors are resolved against the persisted snapshot;
- correction cannot provide evidence ID/value/hash/provenance;
- correction runs `EvidenceVerifier` and never `SemanticVerifier`;
- invalid correction returns typed validation failure and leaves case/version/history unchanged;
- successful correction appends a human-correction revision and terminal decision atomically;
- stale `expected_version` and terminal-case decisions return conflict;
- same decision idempotency key/fingerprint returns the original result; changed payload conflicts.

- [ ] **Step 4: Implement safe correction rebuilding**

Create one private `build_corrected_report(snapshot, current_report, draft)` function that:

1. validates critical span IDs against the stored view;
2. resolves every `span_id::field_path` selector through a new local catalog;
3. assigns evidence IDs and hashes through `EvidenceCatalog.resolve`;
4. builds claims from the resolved IDs;
5. preserves trace/run binding and original diagnoser kind;
6. writes `human-correction-v1` provenance and a `RevisionOrigin.HUMAN_CORRECTION` revision;
7. creates a `VerificationInput` with the rebuilt report/hash;
8. calls deterministic verification and accepts only `verified`.

Do not use model-generated prose or call a provider during correction.

- [ ] **Step 5: Run GREEN and Phase 2 service regressions**

Run:

```powershell
.venv\Scripts\python -m pytest tests/review/test_service.py tests/diagnosis/test_service.py -q
.venv\Scripts\python -m ruff check src/afc/review tests/review
.venv\Scripts\python -m mypy
```

Expected: all commands PASS.

- [ ] **Step 6: Commit Task 6**

```powershell
git add src/afc/review/service.py src/afc/review/protocols.py tests/review/test_service.py
git commit -m "feat: add diagnosis review use cases"
```

## Task 7: Bounded LangGraph Workflow, Lease, and Resume

**Files:**

- Create: `src/afc/review/workflow.py`
- Modify: `src/afc/review/protocols.py`
- Create: `tests/review/test_workflow.py`
- Create: `tests/review/test_workflow_recovery.py`

**Interfaces:**

```python
class ReviewWorkflowState(TypedDict):
    case_id: str
    verification_round: int
    composite_verdict: str | None


class ReviewWorkflow:
    async def run(self, case_id: str) -> DiagnosisReviewDetail:
        raise NotImplementedError

    async def resume(self, case_id: str) -> DiagnosisReviewDetail:
        raise NotImplementedError
```

Compile one `StateGraph` with explicit nodes `verify_initial`, `request_revision`, `revise_once`, `verify_final`, and `route_to_human`. Every node reloads durable state and performs exactly one repository transaction around each state boundary. The graph state contains routing hints only; it is never the recovery record.

The graph depends on a `ReviewReviser` protocol with `supports(diagnoser_kind)` and `revise(runtime_bundle, evidence_gaps)` methods. Workflow tests use a fake implementation; Task 8 supplies the production adapter over optional `RevisionCapableDiagnoser` instances.

- [ ] **Step 1: Write failing happy-path workflow tests**

Using fakes for the diagnoser/verifiers and a real temporary SQLite repository, cover:

```text
pending -> verifying -> awaiting_human_review
deterministic verified, no semantic invocation
hybrid deterministic verified + semantic verified
hybrid deterministic hard finding skips semantic
all nonterminal paths end at awaiting_human_review
event sequence matches state/version sequence
```

- [ ] **Step 2: Run RED for graph routing**

Run: `.venv\Scripts\python -m pytest tests/review/test_workflow.py -q`

Expected: FAIL because the workflow graph is absent.

- [ ] **Step 3: Implement initial verification and verdict routing**

`verify_initial` must claim `pending_verification` with CAS, persist `verifying` and a lease, load a private `ReviewRuntimeBundle`, construct `VerificationInput`, run deterministic verification, optionally run semantic verification only after deterministic pass in hybrid mode, persist each verifier run separately, merge via the pure truth table, and route non-revision outcomes to human review.

For deterministic mode, a deterministic pass means composite `verified` but still transitions to `awaiting_human_review`. For any deterministic hard finding, semantic verification is skipped.

- [ ] **Step 4: Write failing bounded-revision tests**

Cover:

- `needs_evidence` + revision-capable diagnoser + revision count 0 enters requested/revising;
- revision 1 is append-only and points to revision 0 hash/gap IDs;
- full deterministic and optional semantic verification reruns against revision 1;
- a second `needs_evidence` result goes directly to human review;
- a rules report never enters revision states;
- no code path can persist revision number 2.

- [ ] **Step 5: Implement the complete bounded graph**

Conditional edges must be based on persisted case status/revision count and the pure composite verdict, not on provider prose. `route_to_human` clears the lease, stores the final composite verdict, increments case version, and appends the event in one transaction.

- [ ] **Step 6: Write failing crash/recovery/provider-error tests**

Use an injected clock and deterministic lease duration. Cover:

- crash after `verifying` commit but before verifier result;
- crash after `revising` commit but before revision write;
- resume before lease expiry -> conflict;
- resume after expiry -> one new claim and eventual human-review state;
- resume of untouched `pending_verification` succeeds;
- resume of `awaiting_human_review` or terminal state conflicts without provider call;
- duplicate provider completion does not duplicate verifier run/revision/event;
- semantic provider configuration/protocol/request failure persists an operational semantic verifier run, `provider_failed` event, and `awaiting_human_review` before raising a typed failure containing `case_id`, stable code, and retryability;
- revision-provider failure persists a `revision_provider_failed` event with redacted operational metadata and routes to human review without fabricating a verifier run;
- a restarted service instance can GET and resume from the same database.

- [ ] **Step 7: Run RED then implement recovery**

Run RED: `.venv\Scripts\python -m pytest tests/review/test_workflow_recovery.py -q`

Expected: FAIL on lease/resume and provider persistence.

Implement recovery so external calls happen only after the lease transaction commits. Document in the workflow docstring that crash recovery provides at-least-once model invocation and exactly-once persisted domain effects, not exactly-once provider billing.

- [ ] **Step 8: Run GREEN and graph regressions**

Run:

```powershell
.venv\Scripts\python -m pytest tests/review/test_workflow.py tests/review/test_workflow_recovery.py tests/supportlab/test_graph.py -q
.venv\Scripts\python -m ruff check src/afc/review/workflow.py tests/review/test_workflow.py tests/review/test_workflow_recovery.py
.venv\Scripts\python -m mypy
```

Expected: all commands PASS.

- [ ] **Step 9: Commit Task 7**

```powershell
git add src/afc/review/workflow.py src/afc/review/protocols.py tests/review/test_workflow.py tests/review/test_workflow_recovery.py
git commit -m "feat: orchestrate bounded diagnosis reviews"
```

## Task 8: Optional RevisionCapableDiagnoser and LLM Evidence Revision

**Files:**

- Modify: `src/afc/diagnosis/protocols.py`
- Modify: `src/afc/diagnosis/llm_diagnoser.py`
- Create: `src/afc/review/reviser.py`
- Modify: `src/afc/review/workflow.py`
- Create: `tests/diagnosis/test_llm_revision.py`
- Modify: `tests/review/test_workflow.py`

**Interfaces:**

```python
@runtime_checkable
class RevisionCapableDiagnoser(Diagnoser, Protocol):
    async def revise(
        self,
        view: DiagnosticTraceView,
        evidence: EvidenceCatalog,
        previous_report: DiagnosisReport,
        evidence_gaps: tuple[EvidenceGap, ...],
    ) -> DiagnosisExecution:
        raise NotImplementedError
```

Use `TYPE_CHECKING` and postponed annotations in diagnosis modules to avoid a runtime import cycle with `afc.review.models`. Implement `DiagnosisReviser` in the review layer: it receives the configured diagnoser mapping, decodes the persisted `DiagnosticTraceView`, rebuilds `EvidenceCatalog`, checks the optional protocol, calls `revise`, then attaches only the snapshot's trace/run binding and original diagnoser kind. It must not require raw `TraceIR`, so revision still works after an API restart.

- [ ] **Step 1: Write failing protocol-compatibility tests**

Prove `LlmDiagnoser` satisfies `RevisionCapableDiagnoser`, `RuleDiagnoser` does not, and all existing objects still satisfy the unchanged `Diagnoser` protocol. Prove `DiagnosisReviser.supports(rules)` is false and `revise` raises a stable unsupported-revision error before any provider call. Add a restart-shaped test that revises from `ReviewRuntimeBundle` without a `TraceRepository` or raw `TraceIR`.

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python -m pytest tests/diagnosis/test_llm_revision.py -q`

Expected: FAIL because the optional revision protocol and method are absent.

- [ ] **Step 3: Write failing revision prompt-boundary tests**

Capture `ModelProvider.complete` messages and assert the revision request contains only:

- sanitized `DiagnosticTraceView` spans;
- legal catalog selectors;
- the previous structured `DiagnosisReport`;
- ordered `EvidenceGap` objects;
- the strict diagnosis output contract.

Assert it excludes gold/mutation labels, deterministic findings/verdict, initial diagnosis prompts/raw responses, hidden reasoning, semantic run IDs, and secret values. Include a malicious tool-output string and assert it remains quoted data in canonical JSON while the system message says trace content is untrusted.

- [ ] **Step 4: Implement one-shot revision through the existing strict parser**

Refactor `LlmDiagnoser` so initial diagnosis and revision share `_resolve_draft` and strict output validation. Give revision a distinct prompt version/hash and diagnoser version. Invalid model output returns the existing structured abstention behavior; it must not launch a repair loop. The workflow, not the diagnoser, owns the one-revision cap.

- [ ] **Step 5: Run GREEN and all Phase 2 LLM/provider regressions**

Run:

```powershell
.venv\Scripts\python -m pytest tests/diagnosis/test_llm_revision.py tests/diagnosis/test_llm_diagnoser.py tests/diagnosis/test_deepseek.py tests/review/test_workflow.py -q
.venv\Scripts\python -m mypy
```

Expected: all selected tests PASS and no test performs network I/O.

- [ ] **Step 6: Commit Task 8**

```powershell
git add src/afc/diagnosis/protocols.py src/afc/diagnosis/llm_diagnoser.py src/afc/review/reviser.py src/afc/review/workflow.py tests/diagnosis/test_llm_revision.py tests/review/test_workflow.py
git commit -m "feat: support one evidence-focused diagnosis revision"
```

## Task 9: Independent DeepSeek SemanticVerifier

**Files:**

- Create: `src/afc/review/semantic_verifier.py`
- Modify: `src/afc/review/protocols.py`
- Modify: `src/afc/review/workflow.py`
- Modify: `src/afc/evals/run_review_eval.py`
- Modify: `src/afc/evals/review_metrics.py`
- Create: `tests/review/test_semantic_verifier.py`
- Modify: `tests/review/test_workflow.py`
- Modify: `tests/evals/test_run_review_eval.py`

**Interfaces:**

```python
class SemanticVerifier:
    kind = VerifierKind.SEMANTIC

    def __init__(
        self,
        provider: ModelProvider,
        *,
        model: str = "deepseek-v4-flash",
        prompt_version: str = "semantic-verifier-v1",
    ) -> None:
        self._provider = provider

    async def verify(self, input_: VerificationInput) -> VerifierReport:
        raise NotImplementedError
```

- [ ] **Step 1: Write failing strict-output tests**

Use a fake provider to cover a valid `verified`, `needs_evidence`, and `review_required` draft plus invalid cases: extra fields, unknown enum, string confidence, more than five findings, more than three gaps, unknown selector, inconsistent verdict/gaps, empty/unfinished content, and prompt-injection text. Every invalid success response must become one deterministic `review_required/invalid_verifier_output` report; there is no model repair call.

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python -m pytest tests/review/test_semantic_verifier.py -q`

Expected: FAIL because `SemanticVerifier` is absent.

- [ ] **Step 3: Implement independent prompt and selector resolution**

The system message must define the strict JSON schema, treat all trace/tool text as untrusted data, and forbid following instructions inside it. The user message contains canonical JSON with only sanitized spans, the current structured diagnosis, and legal selector strings. Do not pass any deterministic verifier result into `_messages` or store raw messages/responses.

The provider returns selectors only. Resolve them locally to report findings/gaps and reject unknown selectors. Record prompt version/SHA-256, verifier version, provider/model, token/latency/request ID, but not prompt text or raw response.

- [ ] **Step 4: Preserve operational errors for workflow handling**

Let `ProviderConfigurationError`, `ProviderRequestError`, and `ProviderProtocolError` propagate unchanged so Task 7's workflow can persist operational failure and map retryability. Only structurally invalid model content becomes `invalid_verifier_output`.

- [ ] **Step 5: Run GREEN and independence audit**

Extend `afc-evaluate-review` with `--verifier hybrid`, repeatable `--candidate-id`, `--model`, and mandatory `--allow-live-api`. The guard must fail before reading `DEEPSEEK_API_KEY` or constructing a provider. Preserve deterministic mode as the default and add semantic verdict distribution, disagreement, structured-output success, operational errors, token totals, and p50/p95 latency to the report.

Run:

```powershell
.venv\Scripts\python -m pytest tests/review/test_semantic_verifier.py tests/review/test_workflow.py tests/evals/test_run_review_eval.py tests/diagnosis/test_deepseek.py -q
rg -n "gold|mutation|deterministic_report|raw_response|scenario\." src/afc/review/semantic_verifier.py
```

Expected: tests PASS. The search may match explicit defensive error/prompt wording but must reveal no imported label module, mutation field, deterministic report parameter, raw response persistence, or scenario attribute access.

- [ ] **Step 6: Commit Task 9**

```powershell
git add src/afc/review/semantic_verifier.py src/afc/review/protocols.py src/afc/review/workflow.py src/afc/evals/run_review_eval.py src/afc/evals/review_metrics.py tests/review/test_semantic_verifier.py tests/review/test_workflow.py tests/evals/test_run_review_eval.py
git commit -m "feat: add independent semantic diagnosis verifier"
```

## Task 10: FastAPI Review Endpoints and Production Wiring

**Files:**

- Create: `src/afc/api/routes/diagnosis_reviews.py`
- Modify: `src/afc/api/app.py`
- Modify: `src/afc/api/routes/__init__.py`
- Create: `tests/api/test_diagnosis_reviews.py`
- Create: `tests/api/test_diagnosis_review_errors.py`

**Interfaces:**

```text
POST /v1/traces/{trace_id}/diagnosis-reviews
GET  /v1/diagnosis-reviews/{case_id}
POST /v1/diagnosis-reviews/{case_id}/resume
POST /v1/diagnosis-reviews/{case_id}/decisions
```

Create request:

```json
{
  "diagnoser": "rules",
  "verifier": "deterministic",
  "idempotency_key": "review-request-001"
}
```

Decision request includes `action`, `expected_version`, `reviewer_label`, `reason`, optional `correction`, and a required idempotency key. Defaults exist only for create: `rules + deterministic`.

- [ ] **Step 1: Write failing default-offline API test**

Build the app with a temporary SQLite path and a provider fake that fails if called. Ingest a frozen trace, create a review with an empty mode override, assert `201`, `awaiting_human_review`, one immutable revision, one deterministic verifier run, no semantic run, and successful GET after constructing a second app/service instance over the same database.

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python -m pytest tests/api/test_diagnosis_reviews.py -q`

Expected: FAIL because the routes are absent.

- [ ] **Step 3: Wire repository/service/workflow through application lifespan**

Extend `create_app` with injectable `review_repository`/`review_service` seams while preserving existing trace and diagnosis injections. Refactor the current default-service builder into one `build_default_diagnosers()` mapping shared by `DiagnosisService` and `DiagnosisReviser`; do not create two differently configured LLM instances. The default repository path is `AFC_DB_PATH` or `.data/afc.db`. Constructing the module must not open a database connection; initialize schema during application lifespan. Build the default semantic verifier only when DeepSeek configuration exists, but never select it unless the request explicitly asks for `hybrid`.

Include both route prefixes without changing existing `/health`, trace, or diagnosis responses. Return a public aggregate that includes case, ordered revisions, verifier reports, workflow events, and terminal decision, but excludes snapshot JSON, prompt text, provider body, SQL/path details, and secrets.

- [ ] **Step 4: Write failing endpoint contract/error tests**

Cover:

- trace/case missing -> `404` with stable code;
- idempotency mismatch, stale expected version, illegal state, active lease -> `409`;
- malformed correction, unknown selector, failed deterministic correction -> `422` with no case mutation;
- provider permanent/protocol failure after case creation -> persisted human-review case then `502`;
- provider missing/transient retry exhaustion -> persisted human-review case then `503`;
- SQLite/unknown internal error -> `500` without SQL, DB path, trace snapshot, provider body, or key material;
- provider failure body contains `case_id`, stable error code, and `retryable` only;
- duplicate identical create/decision requests return the original result;
- resume of expired work succeeds, while awaiting-human and terminal cases do not call providers.

- [ ] **Step 5: Implement one explicit exception mapper**

Map typed domain/application errors in one adapter-local function. Preserve this response shape for non-validation operational failures:

```json
{
  "detail": {
    "code": "provider_unavailable",
    "case_id": "case-uuid",
    "retryable": true
  }
}
```

Use FastAPI/Pydantic's standard `422` for malformed request bodies and the stable review `422` detail code for semantically invalid corrections.

- [ ] **Step 6: Run GREEN and all API regressions**

Run:

```powershell
.venv\Scripts\python -m pytest tests/api/test_diagnosis_reviews.py tests/api/test_diagnosis_review_errors.py tests/api/test_traces.py tests/api/test_diagnoses.py tests/api/test_health.py -q
.venv\Scripts\python -m mypy
```

Expected: all API tests PASS with no real network access.

- [ ] **Step 7: Commit Task 10**

```powershell
git add src/afc/api/app.py src/afc/api/routes src/afc/review tests/api/test_diagnosis_reviews.py tests/api/test_diagnosis_review_errors.py
git commit -m "feat: expose diagnosis review API"
```

## Task 11: HTTP-Only `afc-review` CLI

**Files:**

- Create: `src/afc/cli/__init__.py`
- Create: `src/afc/cli/review.py`
- Modify: `pyproject.toml`
- Create: `tests/cli/__init__.py`
- Create: `tests/cli/test_review.py`

**Interfaces:**

```text
afc-review create --trace-id ID --diagnoser rules --verifier deterministic --idempotency-key KEY
afc-review show --case-id ID
afc-review resume --case-id ID
afc-review decide --case-id ID --action confirm --expected-version N --reviewer-label LABEL --idempotency-key KEY
```

The base URL is `--api-url` or `AFC_API_URL`, defaulting to `http://127.0.0.1:8000`. Correction input is a UTF-8 JSON file supplied through `--correction-file`; the CLI does not synthesize evidence fields.

- [ ] **Step 1: Write failing parser and HTTP-adapter tests**

With `httpx.MockTransport`, assert exact method/path/body for create/show/resume/decide, canonical single-line JSON to stdout, no SQLite imports/access, stable non-zero exit codes, timeout/network error redaction, and no provider body echo.

- [ ] **Step 2: Write failing paid-call guard tests**

`create --diagnoser deepseek` or `create --verifier hybrid` must exit before any HTTP call unless `--allow-live-api` is present. Offline create/show/resume/decide never require the flag. The CLI never reads `DEEPSEEK_API_KEY`; the API process owns provider configuration.

- [ ] **Step 3: Run RED**

Run: `.venv\Scripts\python -m pytest tests/cli/test_review.py -q`

Expected: FAIL because `afc.cli.review` and the console script are absent.

- [ ] **Step 4: Implement thin client and stable exits**

Use `argparse`, `httpx.Client`, a bounded timeout, and one request helper. Suggested exits:

```text
0 success
2 local usage/live-guard error
3 API 4xx conflict/validation/not-found
4 API/provider 5xx or transport failure
```

Register `afc-review = "afc.cli.review:main"`. Emit API JSON as canonical UTF-8 with sorted keys; put concise redacted diagnostics on stderr.

- [ ] **Step 5: Run GREEN and installed-entrypoint smoke**

Run:

```powershell
.venv\Scripts\python -m pytest tests/cli/test_review.py -q
.venv\Scripts\afc-review.exe --help
.venv\Scripts\python -m ruff check src/afc/cli tests/cli
.venv\Scripts\python -m mypy
```

Expected: all commands PASS; help lists four subcommands and the explicit live flag.

- [ ] **Step 6: Commit Task 11**

```powershell
git add src/afc/cli pyproject.toml tests/cli
git commit -m "feat: add diagnosis review CLI"
```

## Task 12: Docker Persistence, Security Gates, Documentation, and Live Evidence

**Files:**

- Modify: `Dockerfile`
- Modify: `compose.yaml`
- Modify: `.gitignore`
- Modify: `.env.example`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Create: `docs/evaluation/phase3-verification-review.md`
- Modify: `tests/test_delivery_config.py`
- Create: `tests/review/test_secret_hygiene.py`

**Interfaces and delivery contract:**

- Local default database: `.data/afc.db`.
- Container database: `/data/afc.db` through `AFC_DB_PATH`.
- Named Compose volume: `afc_data:/data`.
- Runtime remains UID/GID `10001:10001`; `/data` is writable by that user.
- CI runs only deterministic review generation/evaluation. Paid semantic evaluation remains an explicitly approved manual experiment.

- [ ] **Step 1: Write failing delivery and secret-hygiene tests**

Assert:

- Dockerfile creates/chowns `/data` before switching user;
- Compose sets `AFC_DB_PATH=/data/afc.db` and mounts `afc_data`;
- `.data/` and generated live reports are ignored;
- tracked source/docs/fixtures contain no DeepSeek key pattern;
- temporary SQLite contents, workflow events, API JSON, CLI JSON, verifier reports, and raised errors contain neither a sentinel key nor raw provider response;
- existing non-root and pinned-image delivery assertions remain true.

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python -m pytest tests/test_delivery_config.py tests/review/test_secret_hygiene.py -q`

Expected: FAIL on missing `/data` persistence and security assertions.

- [ ] **Step 3: Implement container persistence and CI deterministic gates**

Update CI after existing Phase 2 gates to:

1. regenerate the review dataset in `.cache` and compare all three hashes;
2. run deterministic review evaluation twice and compare bytes;
3. build/start the API with a named test volume;
4. ingest one frozen trace, create a default review, GET it, confirm it;
5. restart only the API container without deleting the volume;
6. GET the same terminal case and assert its revision/verifier/event/decision chain remains intact;
7. assert container UID/GID and `/data` ownership;
8. remove the test volume only after the recovery assertion and always print logs on failure.

Do not add a DeepSeek key or live call to GitHub Actions.

- [ ] **Step 4: Update user-facing and evaluation documentation**

README must show:

- default offline create/show/confirm flow through API and CLI;
- `AFC_DB_PATH` and Docker persistence behavior;
- one-revision bound and mandatory human decision;
- recovery/resume semantics and at-least-once provider billing caveat;
- explicit `--allow-live-api` requirement;
- data-minimization and reviewer-label-not-authentication caveats.

`docs/evaluation/phase3-verification-review.md` must record exact commit SHA, dataset/manifest hashes, 20/20 valid pass, 16/16 defect recall, 6/6 unsupported protection, byte-exact check, SQLite restart result, full test/lint/type/container gates, and semantic experiment usage/distribution/error data. Do not claim a live result before running it.

- [ ] **Step 5: Run the complete offline quality gate**

Run from a clean implementation worktree:

```powershell
uv sync --frozen --group dev
uv run ruff check src tests
uv run mypy
uv run pytest --cov=afc --cov-report=term-missing
uv run afc-generate-dataset --output .cache/phase3-phase1-check --seed 20260715
uv run afc-evaluate-diagnosis --output .cache/phase3-rules-a.json
uv run afc-evaluate-diagnosis --output .cache/phase3-rules-b.json
uv run afc-generate-review-dataset --output .cache/phase3-review-dataset --seed 20260717
uv run afc-evaluate-review --output .cache/phase3-review-a.json
uv run afc-evaluate-review --output .cache/phase3-review-b.json
uv run python -c "from pathlib import Path; assert Path('.cache/phase3-rules-a.json').read_bytes()==Path('.cache/phase3-rules-b.json').read_bytes(); assert Path('.cache/phase3-review-a.json').read_bytes()==Path('.cache/phase3-review-b.json').read_bytes()"
docker compose config --quiet
docker compose build api
docker compose up --detach --wait --wait-timeout 90 api
```

Then execute the documented API/CLI persistence smoke, restart the API container without deleting `afc_data`, verify the same case, and finally run `docker compose down --volumes --remove-orphans`.

Expected: every gate passes; no command contacts DeepSeek.

- [ ] **Step 6: Request approval and run the controlled semantic experiment**

Before this step, explicitly tell the user that the next commands consume the paid DeepSeek API. Ask them to configure `DEEPSEEK_API_KEY` locally; never request that they paste it into chat.

First run a two-case allowlist smoke consisting of one valid supported report and one mutated/unsupported report. Inspect schema success, independence boundary, verdict, token counts, latency, and errors. Only after the smoke is structurally valid, run the 36-candidate semantic comparison. Generated live reports remain under `evals/reports/generated/` or `.cache/` and are not committed.

Record only aggregate verdict distribution, disagreement rate, structured-output success, operational-error rate, input/output/total tokens, p50/p95 latency, and estimated cost if pricing is explicitly configured. Do not set an unvalidated semantic accuracy threshold.

- [ ] **Step 7: Re-run final gates and inspect the diff**

Run:

```powershell
uv run ruff check src tests
uv run mypy
uv run pytest
git status --short
git diff --check
git diff --stat origin/main...HEAD
git ls-files evals/reports/generated .data
```

Expected: static checks/tests PASS, `git diff --check` is clean, and no runtime database or generated live report is tracked.

- [ ] **Step 8: Commit Task 12**

```powershell
git add Dockerfile compose.yaml .gitignore .env.example .github/workflows/ci.yml README.md docs/evaluation/phase3-verification-review.md tests/test_delivery_config.py tests/review/test_secret_hygiene.py
git commit -m "chore: complete phase 3 delivery gates"
```

---

## Final Acceptance Matrix

Before declaring Phase 3 complete, attach command output or generated-report fields for every row:

| Requirement | Evidence |
|---|---|
| 20/20 valid rule reports pass | deterministic review evaluation metrics |
| 16/16 injected defects detected | per-sample expected/actual finding-code join |
| 6/6 unsupported forced diagnoses blocked | unsupported-scope metric and samples |
| Selector/value/hash verification is exact | focused unit tests and zero missed mutations |
| Revision count never exceeds one | model, repository, and workflow tests |
| Every terminal case has full audit history | API/SQLite terminal-case assertions |
| Stale version/idempotency conflicts detected | repository/API race tests |
| SQLite restart recovery works | second app/container GET of same case |
| Default paths make no external request | fail-on-call provider tests and CI configuration |
| Phase 1/2 behavior remains green | full existing test suite and frozen hashes |
| Deterministic artifacts are byte-exact | two-run byte comparisons |
| Docker persists data as non-root | UID/GID, volume restart, writable-path smoke |
| Semantic path is measured honestly | approved live report aggregate, no claimed threshold |

## Plan Self-Review Checklist

- [x] Every confirmed design section maps to at least one task and test.
- [x] Every production behavior begins with a named RED test and exact command.
- [x] Every task ends with focused GREEN checks and one narrow commit.
- [x] Phase 1/2 frozen files are read-only and regression-tested.
- [x] Deterministic/semantic independence and label-leakage boundaries are explicit.
- [x] SQLite transaction, CAS, lease, idempotency, restart, and append-only semantics are covered.
- [x] Human correction cannot forge evidence and failed correction is non-mutating.
- [x] Provider failure is persisted before a stable 502/503 is returned.
- [x] API and CLI default to offline rules + deterministic verification.
- [x] Docker, CI, secret hygiene, and paid-call consent are included.
- [x] No Phase 4 scope appears: no frontend, Postgres, Redis, durable queue, regression-case generation, repair agent, release gate, auth, or RBAC.
- [x] No implementation begins until this plan is reviewed and explicitly approved.

## Execution Handoff

After plan approval, use `subagent-driven-development` in the isolated Phase 3 implementation worktree, one task and one commit at a time. Recheck PR #1 immediately before creating that worktree. Do not execute any implementation step from this documentation-only planning branch.
