# Phase 2 Evidence Diagnosis MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an evidence-backed, label-leakage-resistant diagnosis pipeline that deterministically diagnoses five SupportLab failure types, abstains on three unsupported types, evaluates all 20 frozen traces, compares an independent DeepSeek diagnoser, and exposes a rule-default diagnosis API.

**Architecture:** A sanitized `DiagnosticTraceView` and local `EvidenceCatalog` form the only inputs to diagnosers. Pure invariant rules and an independent DeepSeek-backed LLM diagnoser produce a shared `DiagnosisExecution`; `DiagnosisService` attaches hidden trace identity only after the decision and provides in-memory idempotency. Evaluation joins predictions with a separate gold sidecar that is never available to diagnosers.

**Tech Stack:** Python 3.12.13, Pydantic 2, FastAPI, httpx, pytest, pytest-asyncio, Ruff, mypy, existing TraceIR v1 and frozen SupportLab dataset.

## Global Constraints

- Do not repeat or modify Phase 1 Task 1–11, final review, main merge, or old worktree cleanup.
- Preserve TraceIR single-root/acyclic/reachable validation and existing 409/422/500 API mappings.
- Preserve explicit `item_skus`, server-side refund calculation, and the rule that deprecated `calculated_amount` never participates in authorization or diagnosis.
- Do not change `evals/datasets/supportlab-v1/traces.jsonl`, `labels.jsonl`, or `manifest.json`; keep their bytes and LF endings exact.
- Diagnosers must never receive `scenario.*`, semantic `run_id`, `trace_id`, `idempotency_key`, `ignore_error`, `calculated_amount`, gold labels, or invariant outputs in the DeepSeek path.
- Supported classes are `wrong_tool`, `invalid_argument`, `policy_violation`, `loop_or_budget_exhaustion`, `invalid_final_state`, and `no_failure`; the other three classes must abstain.
- Automated tests and default CLI/API behavior must not call a paid API.
- Read DeepSeek credentials only from `DEEPSEEK_API_KEY`; never log, commit, trace, report, or image the key.
- Use test-first RED → GREEN → REFACTOR for every production behavior.
- Keep domain modules independent of FastAPI, file system, and provider SDKs.
- Use exact JSON sorting and no wall-clock fields in deterministic rule artifacts.

---

## File Map

```text
src/afc/failure_types.py                 shared taxonomy and supported set
src/afc/diagnosis/models.py              common schemas and invariants
src/afc/diagnosis/trace_view.py          allowlist projection without identity
src/afc/diagnosis/evidence.py            canonical selectors and evidence hashes
src/afc/diagnosis/protocols.py           Diagnoser and ModelProvider protocols
src/afc/diagnosis/rule_diagnoser.py      deterministic decision aggregation
src/afc/diagnosis/service.py             identity attachment and idempotency
src/afc/diagnosis/deepseek.py            bounded OpenAI-compatible HTTP adapter
src/afc/diagnosis/llm_diagnoser.py       prompt, draft parse, evidence resolution
src/afc/diagnosis/errors.py               stable provider/service errors
src/afc/invariants/models.py              rule protocol and result schema
src/afc/invariants/engine.py              pure execution and ruleset hash
src/afc/invariants/supportlab.py          supported rules and unsupported guards
src/afc/evals/diagnosis_labels.py         gold sidecar loading and hash validation
src/afc/evals/diagnosis_metrics.py        deterministic metrics and sample results
src/afc/evals/run_diagnosis_eval.py       offline/live evaluation CLI
src/afc/api/routes/diagnoses.py           POST diagnosis endpoint
evals/datasets/supportlab-v1/diagnosis-labels-v1.jsonl
evals/datasets/supportlab-v1/diagnosis-manifest-v1.json
```

## Task 1: Shared Taxonomy and Diagnosis Schemas

**Files:**
- Create: `src/afc/failure_types.py`
- Create: `src/afc/diagnosis/__init__.py`
- Create: `src/afc/diagnosis/models.py`
- Modify: `src/afc/supportlab/scenarios.py`
- Modify: `src/afc/evals/baselines.py`
- Create: `tests/diagnosis/test_models.py`
- Create: `tests/test_failure_types.py`

**Interfaces:**
- Produces: `FailureType`, `SUPPORTED_DIAGNOSIS_FAILURE_TYPES`, `EvidenceSelector`, `EvidenceRef`, `DiagnosisDecision`, `DiagnosisExecution`, `DiagnosisReport`, `DiagnosisProvenance`, `ProviderUsage`.

- [ ] **Step 1: Write failing taxonomy compatibility test**

```python
from afc.failure_types import FailureType, SUPPORTED_DIAGNOSIS_FAILURE_TYPES
from afc.supportlab.scenarios import FailureType as LegacyFailureType


def test_failure_type_has_one_shared_definition() -> None:
    assert LegacyFailureType is FailureType
    assert FailureType.MISSING_PRECONDITION not in SUPPORTED_DIAGNOSIS_FAILURE_TYPES
    assert FailureType.INVALID_FINAL_STATE in SUPPORTED_DIAGNOSIS_FAILURE_TYPES
```

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python -m pytest tests/test_failure_types.py -q`
Expected: FAIL with `ModuleNotFoundError: afc.failure_types`.

- [ ] **Step 3: Move the enum and preserve the legacy export**

Create `afc.failure_types.FailureType` with all nine existing values and:

```python
SUPPORTED_DIAGNOSIS_FAILURE_TYPES = frozenset(
    {
        FailureType.WRONG_TOOL,
        FailureType.INVALID_ARGUMENT,
        FailureType.POLICY_VIOLATION,
        FailureType.LOOP_OR_BUDGET_EXHAUSTION,
        FailureType.INVALID_FINAL_STATE,
    }
)
```

Import this symbol from `supportlab.scenarios` and `evals.baselines`; remove the old enum definition only.

- [ ] **Step 4: Run GREEN and Phase 1 compatibility tests**

Run: `.venv\Scripts\python -m pytest tests/test_failure_types.py tests/supportlab/test_scenarios.py tests/evals/test_baselines.py -q`
Expected: all selected tests PASS.

- [ ] **Step 5: Write failing diagnosis model invariant tests**

Create the following complete tests, using one shared evidence fixture:

```python
import pytest
from pydantic import ValidationError

from afc.diagnosis.models import (
    AbstainReason,
    ClaimStage,
    DiagnosisClaim,
    DiagnosisDecision,
    DiagnosisProvenance,
    DiagnosisReport,
    DiagnoserKind,
    DiagnosisStatus,
    EvidenceRef,
)
from afc.failure_types import FailureType


EVIDENCE = EvidenceRef(
    evidence_id="ev-1",
    span_id="span-005",
    field_path="attributes.tool.error.type",
    observed_value="RefundRejected",
    value_sha256="a" * 64,
    description="tool rejected",
)
CLAIM = DiagnosisClaim(
    stage=ClaimStage.CAUSE,
    statement="The refund tool rejected the call.",
    evidence_ids=("ev-1",),
)


def test_diagnosed_decision_requires_failure_evidence_and_claim() -> None:
    with pytest.raises(ValidationError):
        DiagnosisDecision(
            status=DiagnosisStatus.DIAGNOSED,
            failure_type=FailureType.POLICY_VIOLATION,
            critical_span_ids=("span-005",),
            confidence=1.0,
        )


def test_no_failure_decision_rejects_critical_spans() -> None:
    with pytest.raises(ValidationError):
        DiagnosisDecision(
            status=DiagnosisStatus.NO_FAILURE,
            failure_type=FailureType.NO_FAILURE,
            critical_span_ids=("span-005",),
            confidence=1.0,
        )


def test_abstained_decision_requires_reason_and_forbids_failure_type() -> None:
    with pytest.raises(ValidationError):
        DiagnosisDecision(
            status=DiagnosisStatus.ABSTAINED,
            failure_type=FailureType.WRONG_TOOL,
            confidence=0.0,
        )


def test_claim_references_must_exist_in_evidence() -> None:
    with pytest.raises(ValidationError):
        DiagnosisDecision(
            status=DiagnosisStatus.DIAGNOSED,
            failure_type=FailureType.POLICY_VIOLATION,
            critical_span_ids=("span-005",),
            causal_chain=(CLAIM,),
            evidence=(),
            confidence=1.0,
        )


def test_report_round_trips_through_json() -> None:
    report = DiagnosisReport(
        trace_id="trace-1",
        run_id="run-1",
        diagnoser=DiagnoserKind.RULES,
        status=DiagnosisStatus.DIAGNOSED,
        failure_type=FailureType.POLICY_VIOLATION,
        critical_span_ids=("span-005",),
        causal_chain=(CLAIM,),
        evidence=(EVIDENCE,),
        confidence=1.0,
        provenance=DiagnosisProvenance(
            taxonomy_version="1.0",
            diagnoser_version="evidence-rules-v1",
            ruleset_version="rules-sha",
        ),
    )
    assert DiagnosisReport.model_validate_json(report.model_dump_json()) == report
```

- [ ] **Step 6: Run RED**

Run: `.venv\Scripts\python -m pytest tests/diagnosis/test_models.py -q`
Expected: FAIL because `afc.diagnosis.models` is missing.

- [ ] **Step 7: Implement strict frozen Pydantic models**

Use `StrEnum`, `ConfigDict(frozen=True, extra="forbid")`, `Field(ge=0, le=1)`, and an `after` validator. Define:

```python
class DiagnosisStatus(StrEnum):
    DIAGNOSED = "diagnosed"
    NO_FAILURE = "no_failure"
    ABSTAINED = "abstained"


class DiagnoserKind(StrEnum):
    RULES = "rules"
    DEEPSEEK = "deepseek"


class AbstainReason(StrEnum):
    UNSUPPORTED_FAILURE_TYPE = "unsupported_failure_type"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    AMBIGUOUS_FINDINGS = "ambiguous_findings"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    INVALID_EVIDENCE_REFERENCE = "invalid_evidence_reference"


class DiagnosisExecution(BaseModel):
    decision: DiagnosisDecision
    provenance: DiagnosisProvenance
    usage: ProviderUsage | None = None


class DiagnosisReport(DiagnosisDecision):
    schema_version: Literal["1.0"] = "1.0"
    trace_id: str
    run_id: str
    diagnoser: DiagnoserKind
    provenance: DiagnosisProvenance
    usage: ProviderUsage | None = None
```

The validator enforces the three state invariants and verifies every claim evidence ID exists.

- [ ] **Step 8: Run GREEN, lint, and type check**

Run: `.venv\Scripts\python -m pytest tests/diagnosis/test_models.py tests/test_failure_types.py -q`
Expected: PASS.

Run: `.venv\Scripts\python -m ruff check src/afc/failure_types.py src/afc/diagnosis tests/diagnosis tests/test_failure_types.py`
Expected: `All checks passed!`

Run: `.venv\Scripts\python -m mypy src/afc/failure_types.py src/afc/diagnosis`
Expected: success with no issues.

- [ ] **Step 9: Commit**

```powershell
git add src/afc/failure_types.py src/afc/diagnosis src/afc/supportlab/scenarios.py src/afc/evals/baselines.py tests/diagnosis tests/test_failure_types.py
git commit -m "feat: define diagnosis domain models"
```

## Task 2: Label-Safe Trace View and Evidence Catalog

**Files:**
- Create: `src/afc/diagnosis/trace_view.py`
- Create: `src/afc/diagnosis/evidence.py`
- Create: `tests/diagnosis/test_trace_view.py`
- Create: `tests/diagnosis/test_evidence.py`

**Interfaces:**
- Produces: `DiagnosticSpan`, `DiagnosticTraceView.from_trace(trace)`, `EvidenceCatalog.from_view(view)`, `EvidenceCatalog.resolve(selector, description)`.

- [ ] **Step 1: Write RED tests for leakage removal and immutability**

Load `invalid_argument-01` from the frozen JSONL and assert the view has no `trace_id` or `run_id`, and no attribute key containing `scenario`, `idempotency_key`, `ignore_error`, or `calculated_amount`. Assert source `TraceIR.model_dump()` is unchanged and spans are stably sorted.

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python -m pytest tests/diagnosis/test_trace_view.py -q`
Expected: FAIL because `trace_view` is missing.

- [ ] **Step 3: Implement the explicit allowlist projection**

```python
ALLOWED_ATTRIBUTES = frozenset(
    {
        "run.outcome",
        "run.final_message",
        "tool.name",
        "tool.arguments.customer_id",
        "tool.arguments.order_id",
        "tool.arguments.item_skus",
        "tool.arguments.amount",
        "tool.arguments.approval",
        "tool.arguments.reason",
        "tool.result",
        "tool.error.type",
        "tool.error.message",
    }
)
```

`DiagnosticTraceView` contains only `spans: tuple[DiagnosticSpan, ...]`; it has no correlation identity fields.

- [ ] **Step 4: Run GREEN**

Run: `.venv\Scripts\python -m pytest tests/diagnosis/test_trace_view.py -q`
Expected: PASS.

- [ ] **Step 5: Write RED Evidence Catalog tests**

Verify selectors `span-005::attributes.tool.error.type`, `span-005::name`, and `span-000::status`; exact observed value; stable SHA-256; deterministic ordering; duplicate selector rejection; unknown selector `KeyError`; and no forbidden selector.

- [ ] **Step 6: Run RED**

Run: `.venv\Scripts\python -m pytest tests/diagnosis/test_evidence.py -q`
Expected: FAIL because `EvidenceCatalog` is missing.

- [ ] **Step 7: Implement canonical catalog**

Serialize values with:

```python
json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

Build selector IDs as `ev-` plus the first 16 hex characters of SHA-256 over the selector string. Resolve values locally and never accept an LLM-supplied observed value.

- [ ] **Step 8: Run GREEN and static checks**

Run: `.venv\Scripts\python -m pytest tests/diagnosis/test_trace_view.py tests/diagnosis/test_evidence.py -q`
Expected: PASS.

Run: `.venv\Scripts\python -m ruff check src/afc/diagnosis tests/diagnosis && .venv\Scripts\python -m mypy src/afc/diagnosis`
Expected: both commands succeed.

- [ ] **Step 9: Commit**

```powershell
git add src/afc/diagnosis/trace_view.py src/afc/diagnosis/evidence.py tests/diagnosis/test_trace_view.py tests/diagnosis/test_evidence.py
git commit -m "feat: build label-safe diagnosis evidence view"
```

## Task 3: Phase 2 Gold Sidecar

**Files:**
- Create: `src/afc/evals/diagnosis_labels.py`
- Create: `tests/evals/test_diagnosis_labels.py`
- Create: `evals/datasets/supportlab-v1/diagnosis-labels-v1.jsonl`
- Create: `evals/datasets/supportlab-v1/diagnosis-manifest-v1.json`

**Interfaces:**
- Produces: `DiagnosisGoldLabel`, `DiagnosisDatasetManifest`, `load_diagnosis_labels(path)`, `validate_dataset_join(traces, labels)`, `build_diagnosis_manifest(path)`.

- [ ] **Step 1: Write RED schema and join tests**

Require 20 unique run IDs, exact join with frozen traces, 14 supported dispositions and 6 unsupported abstentions. Reject duplicate/missing/extra run IDs and selectors whose span IDs do not exist.

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python -m pytest tests/evals/test_diagnosis_labels.py -q`
Expected: FAIL because loader and sidecar are missing.

- [ ] **Step 3: Implement strict loader and manifest hash**

Use `Path.read_bytes()`, one `DiagnosisGoldLabel.model_validate_json(line)` per non-empty line, and SHA-256 of exact sidecar bytes. Manifest fields are `name`, `schema_version`, `label_count`, and `labels_sha256`.

- [ ] **Step 4: Add all 20 gold records**

Use these exact annotation rules for both `-01` and `-02` cases:

```python
ANNOTATIONS = {
    "wrong_tool": ("diagnosed", ("span-001",), ("span-001::name", "span-001::status")),
    "invalid_argument": ("diagnosed", ("span-005",), ("span-005::attributes.tool.arguments.amount", "span-004::attributes.tool.result", "span-005::attributes.tool.error.message")),
    "policy_violation": ("diagnosed", ("span-005",), ("span-005::attributes.tool.arguments.approval", "span-005::attributes.tool.error.message")),
    "loop_or_budget_exhaustion": ("diagnosed", ("span-008",), ("span-000::attributes.run.outcome", "span-008::name")),
    "invalid_final_state": ("diagnosed", ("span-000",), ("span-000::attributes.run.final_message", "span-005::attributes.tool.result")),
    "no_failure": ("no_failure", (), ()),
    "missing_precondition": ("abstained", (), ()),
    "ignored_tool_error": ("abstained", (), ()),
    "context_corruption": ("abstained", (), ()),
}
```

The committed JSONL follows the frozen label order, includes a concise rationale, uses LF, and is not generated from `scenario.expected_failure` at diagnosis runtime.

- [ ] **Step 5: Generate the exact manifest and run GREEN**

Run a repository-local Python command that calls
`build_diagnosis_manifest(Path("evals/datasets/supportlab-v1/diagnosis-labels-v1.jsonl")) -> DiagnosisDatasetManifest`,
serialize its `model_dump(mode="json")` with sorted keys plus a final newline, then run:

Run: `.venv\Scripts\python -m pytest tests/evals/test_diagnosis_labels.py tests/evals/test_generate_dataset.py -q`
Expected: PASS and Phase 1 golden fixtures remain unchanged.

- [ ] **Step 6: Commit**

```powershell
git add src/afc/evals/diagnosis_labels.py tests/evals/test_diagnosis_labels.py evals/datasets/supportlab-v1/diagnosis-labels-v1.jsonl evals/datasets/supportlab-v1/diagnosis-manifest-v1.json
git commit -m "test: add Phase 2 diagnosis gold labels"
```

## Task 4: Invariant Engine

**Files:**
- Create: `src/afc/invariants/__init__.py`
- Create: `src/afc/invariants/models.py`
- Create: `src/afc/invariants/engine.py`
- Create: `tests/invariants/test_engine.py`

**Interfaces:**
- Produces: `RuleContext(view, evidence)`, `InvariantRule.evaluate(context)`, `InvariantResult`, `InvariantEngine.run(context)`, `InvariantEngine.ruleset_version`.

- [ ] **Step 1: Write RED engine tests**

Use two tiny fake pure rules to assert registration-order-independent stable result sorting, `passed/failed/not_applicable`, duplicate `rule_id@version` rejection, exception propagation, and a stable ruleset SHA-256.

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python -m pytest tests/invariants/test_engine.py -q`
Expected: FAIL because `afc.invariants` is missing.

- [ ] **Step 3: Implement rule protocol and engine**

```python
class InvariantRule(Protocol):
    rule_id: str
    rule_version: str

    def evaluate(self, context: RuleContext) -> InvariantResult:
        raise NotImplementedError


class InvariantEngine:
    def run(self, context: RuleContext) -> tuple[InvariantResult, ...]:
        return tuple(
            sorted(
                (rule.evaluate(context) for rule in self.rules),
                key=lambda result: (result.rule_id, result.rule_version),
            )
        )
```

`InvariantResult` includes status, severity, optional failure type, scope, evidence tuple, explanation, and hard_failure. Sort results by `(rule_id, rule_version)`.

- [ ] **Step 4: Run GREEN and static checks**

Run: `.venv\Scripts\python -m pytest tests/invariants/test_engine.py -q`
Expected: PASS.

Run: `.venv\Scripts\python -m ruff check src/afc/invariants tests/invariants && .venv\Scripts\python -m mypy src/afc/invariants`
Expected: both succeed.

- [ ] **Step 5: Commit**

```powershell
git add src/afc/invariants tests/invariants
git commit -m "feat: add deterministic invariant engine"
```

## Task 5: Five Supported SupportLab Rules

**Files:**
- Create: `src/afc/invariants/supportlab.py`
- Create: `tests/invariants/test_supportlab_rules.py`

**Interfaces:**
- Produces: `KnownToolRule`, `SubmitRefundArgumentsRule`, `SubmitRefundPolicyRule`, `StepBudgetRule`, `FinalStateRule`, `supported_rules()`.

- [ ] **Step 1: Write one RED test per supported failure and clean counterexample**

Load frozen traces by run ID. For each rule assert the target pair fails with the exact failure type and evidence selectors, while `clean-01` passes. Also assert the argument rule passes `policy_violation-01` and `context_corruption-01`, preventing rule overlap.

- [ ] **Step 2: Run RED**

Run: `.venv\Scripts\python -m pytest tests/invariants/test_supportlab_rules.py -q`
Expected: FAIL because SupportLab rules are missing.

- [ ] **Step 3: Implement minimal deterministic rules**

- Known tool: tool span name not in the six SupportLab tools.
- Invalid argument: submitted amount differs from the preceding `calculate_refund` result or the error code contains `amount_exceeds_calculation`; never inspect approval, customer mismatch, or calculated_amount.
- Policy: `submit_refund` has approval `none` and `RefundRejected/missing_approval` while the root run fails.
- Loop: root outcome `step_limit` and at least two repeated tool names; put the last repeated span first in evidence.
- Final state: root outcome succeeded, submit_refund succeeded, and final message asserts success without a refund record.

Use only `DiagnosticTraceView` and `EvidenceCatalog`.

- [ ] **Step 4: Run GREEN and full rule matrix**

Run: `.venv\Scripts\python -m pytest tests/invariants/test_supportlab_rules.py -q`
Expected: PASS for all target and counterexample cases.

- [ ] **Step 5: Commit**

```powershell
git add src/afc/invariants/supportlab.py tests/invariants/test_supportlab_rules.py
git commit -m "feat: diagnose supported SupportLab invariants"
```

## Task 6: Unsupported Guards, Rule Diagnoser, and Service

**Files:**
- Create: `src/afc/diagnosis/protocols.py`
- Create: `src/afc/diagnosis/rule_diagnoser.py`
- Create: `src/afc/diagnosis/service.py`
- Modify: `src/afc/invariants/supportlab.py`
- Create: `tests/invariants/test_scope_guards.py`
- Create: `tests/diagnosis/test_rule_diagnoser.py`
- Create: `tests/diagnosis/test_service.py`

**Interfaces:**
- Produces: three guard rules, `Diagnoser.diagnose(view, evidence) -> DiagnosisExecution`, `RuleDiagnoser`, `DiagnosisService.diagnose(trace, kind, idempotency_key=None)`.

- [ ] **Step 1: Write RED guard tests**

Assert missing policy before submit, tool error followed by root success, and customer mismatch each produce `unsupported_guard` findings. Assert all 14 supported traces do not trigger any guard.

- [ ] **Step 2: Run RED and implement guards**

Run: `.venv\Scripts\python -m pytest tests/invariants/test_scope_guards.py -q`
Expected: FAIL, then implement and rerun to PASS.

- [ ] **Step 3: Write RED aggregation tests**

Cover unsupported guard precedence, one supported finding, two-type ambiguity, failed root with no known evidence, and clean run. Verify `critical_span_ids`, claim evidence IDs, confidence, and deterministic ordering.

- [ ] **Step 4: Implement RuleDiagnoser**

Apply the exact precedence from design section 8.4. Return a `DiagnosisExecution` with `diagnoser_version="evidence-rules-v1"`, engine ruleset hash, no model/prompt, and no usage.

- [ ] **Step 5: Write RED service identity/idempotency tests**

Use a fake diagnoser that records its view. Assert it cannot access trace/run identity; the final report contains both IDs; same fingerprint returns the cached report; same idempotency key with a different trace raises `DiagnosisConflictError`.

- [ ] **Step 6: Implement service and run GREEN**

Fingerprint canonical TraceIR JSON + diagnoser kind + `diagnoser.version_fingerprint`. Protect cache maps with an `asyncio.Lock`; cache only completed reports.

Run: `.venv\Scripts\python -m pytest tests/invariants/test_scope_guards.py tests/diagnosis/test_rule_diagnoser.py tests/diagnosis/test_service.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/afc/diagnosis/protocols.py src/afc/diagnosis/rule_diagnoser.py src/afc/diagnosis/service.py src/afc/invariants/supportlab.py tests/invariants/test_scope_guards.py tests/diagnosis/test_rule_diagnoser.py tests/diagnosis/test_service.py
git commit -m "feat: assemble evidence-backed rule diagnoses"
```

## Task 7: Deterministic Evaluation and Hard Gate

**Files:**
- Create: `src/afc/evals/diagnosis_metrics.py`
- Create: `src/afc/evals/run_diagnosis_eval.py`
- Modify: `pyproject.toml`
- Create: `tests/evals/test_diagnosis_metrics.py`
- Create: `tests/evals/test_run_diagnosis_eval.py`

**Interfaces:**
- Produces: `evaluate_diagnoser(*, traces: tuple[TraceIR, ...], labels: tuple[DiagnosisGoldLabel, ...], service: DiagnosisService, kind: DiagnoserKind) -> DiagnosisEvaluationReport`, canonical artifact writer, `afc-evaluate-diagnosis` CLI.

- [ ] **Step 1: Write RED metric unit tests with hand-calculated samples**

Assert supported accuracy, critical Top-1, evidence validity/hit/precision, clean false-positive rate, unsupported abstain rate, coverage, and partial status. Denominators of zero serialize as `null`, not zero.

- [ ] **Step 2: Implement metrics and run GREEN**

Run: `.venv\Scripts\python -m pytest tests/evals/test_diagnosis_metrics.py -q`
Expected: RED before implementation, then PASS.

- [ ] **Step 3: Write RED 20-trace acceptance test**

Run the real RuleDiagnoser over the frozen traces and sidecar. Assert `14/14`, `10/10`, `0/4` false positives, `6/6` unsupported abstentions, 100% valid selectors, and at least one gold evidence hit for every supported failure.

- [ ] **Step 4: Implement evaluator, weak baseline adapters, and stable writer**

Write JSON as `json.dumps(report.model_dump(mode="json"), sort_keys=True, separators=(",", ":")) + "\n"`. Rule artifacts exclude wall-clock time and measured latency. Preserve Phase 1 baseline implementations and label them only in the evaluation adapter.

- [ ] **Step 5: Verify byte-exact determinism**

Run the CLI twice to two temporary files and compare SHA-256. Expected: hashes are identical.

- [ ] **Step 6: Run tests and commit**

Run: `.venv\Scripts\python -m pytest tests/evals/test_diagnosis_metrics.py tests/evals/test_run_diagnosis_eval.py tests/evals/test_baselines.py -q`
Expected: PASS.

```powershell
git add src/afc/evals/diagnosis_metrics.py src/afc/evals/run_diagnosis_eval.py pyproject.toml tests/evals/test_diagnosis_metrics.py tests/evals/test_run_diagnosis_eval.py
git commit -m "feat: evaluate evidence diagnosis deterministically"
```

## Task 8: DeepSeek Provider Contract

**Files:**
- Create: `src/afc/diagnosis/errors.py`
- Create: `src/afc/diagnosis/deepseek.py`
- Modify: `src/afc/diagnosis/protocols.py`
- Create: `tests/diagnosis/test_deepseek.py`

**Interfaces:**
- Produces: `ChatMessage`, `GenerationConfig`, `ProviderResponse`, `ModelProvider.complete(messages: tuple[ChatMessage, ...], config: GenerationConfig) -> ProviderResponse`, `DeepSeekConfig.from_env() -> DeepSeekConfig`, `DeepSeekProvider`.

- [ ] **Step 1: Write RED request contract test**

With `httpx.MockTransport`, assert POST `https://api.deepseek.com/chat/completions`, bearer header, `model=deepseek-v4-flash`, non-streaming, JSON output, thinking disabled, bounded max tokens, and no key in repr/logged exception.

- [ ] **Step 2: Implement minimal adapter and run GREEN**

Use an injected `httpx.AsyncClient` and injected async sleeper. Parse `choices[0].message.content`, finish reason, response ID, model, and usage.

- [ ] **Step 3: Write RED retry/error matrix**

Assert one retry for 429/500/503/timeout; no retry for 400/401/402/422; empty/malformed provider envelope raises `ProviderProtocolError`; missing key raises `ProviderConfigurationError` before HTTP.

- [ ] **Step 4: Implement bounded retry and stable errors**

Never include response body or Authorization header in user-facing exception text. Store numeric status and AFC error code separately.

- [ ] **Step 5: Run GREEN and commit**

Run: `.venv\Scripts\python -m pytest tests/diagnosis/test_deepseek.py -q`
Expected: PASS with no real network call.

```powershell
git add src/afc/diagnosis/errors.py src/afc/diagnosis/deepseek.py src/afc/diagnosis/protocols.py tests/diagnosis/test_deepseek.py
git commit -m "feat: add bounded DeepSeek provider"
```

## Task 9: Independent LLM Diagnoser

**Files:**
- Create: `src/afc/diagnosis/llm_diagnoser.py`
- Create: `tests/diagnosis/test_llm_diagnoser.py`

**Interfaces:**
- Produces: `LlmDiagnoser(provider, model, prompt_version)`, prompt builder, strict draft schema, local selector resolution.

- [ ] **Step 1: Write RED prompt isolation test**

Use a recording fake provider and a frozen trace whose run ID and removed attributes contain `invalid_argument`. Assert serialized messages contain none of: `invalid_argument-01`, `scenario.expected_failure`, `idempotency_key`, `ignore_error`, `calculated_amount`, `trace_id`, or invariant results. Assert the supported taxonomy and `abstained` semantics are present.

- [ ] **Step 2: Write RED valid draft test**

Return a JSON draft with `span-005::attributes.tool.error.message`; assert local resolution supplies observed value/hash, provenance contains prompt SHA-256/model, and usage is copied.

- [ ] **Step 3: Implement prompt, draft models, and resolution**

The draft includes status, optional supported failure type, critical span IDs, claims whose evidence entries are selectors, confidence, and optional abstain reason. It never accepts observed values from the provider.

- [ ] **Step 4: Write and implement invalid output tests**

Empty content, invalid JSON, extra fields, unsupported diagnosed type, and invalid state become `abstained/invalid_model_output`; nonexistent selectors become `abstained/invalid_evidence_reference`. Provider operational errors propagate.

- [ ] **Step 5: Run GREEN and commit**

Run: `.venv\Scripts\python -m pytest tests/diagnosis/test_llm_diagnoser.py tests/diagnosis/test_deepseek.py -q`
Expected: PASS.

```powershell
git add src/afc/diagnosis/llm_diagnoser.py tests/diagnosis/test_llm_diagnoser.py
git commit -m "feat: constrain LLM diagnosis to trace evidence"
```

## Task 10: Rule-Default Diagnosis API

**Files:**
- Create: `src/afc/api/routes/diagnoses.py`
- Modify: `src/afc/api/app.py`
- Create: `tests/api/test_diagnoses.py`

**Interfaces:**
- Produces: `POST /v1/traces/{trace_id}/diagnoses` with body `{diagnoser, idempotency_key}` and `DiagnosisReport` response.

- [ ] **Step 1: Write RED API happy-path tests**

Create a trace through the repository, request an empty body, assert rule mode is default and returns 200 with a validated report. Assert no DeepSeek provider is constructed or called.

- [ ] **Step 2: Implement injectable route and app wiring**

`create_app` accepts optional `DiagnosisService`; otherwise build the default SupportLab rule engine/service. The route loads `TraceRepository.get(trace_id)` and calls the service.

- [ ] **Step 3: Write RED error mapping tests**

Cover 404 trace, 422 request, 409 idempotency conflict, 503 unconfigured DeepSeek, 502 permanent provider error, 503 exhausted transient provider error, 200 semantic abstain, and preserved trace ingestion behavior.

- [ ] **Step 4: Implement stable mappings and run GREEN**

Do not expose provider bodies or secrets. Unexpected errors remain 500.

Run: `.venv\Scripts\python -m pytest tests/api/test_diagnoses.py tests/api/test_traces.py tests/api/test_health.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/afc/api/routes/diagnoses.py src/afc/api/app.py tests/api/test_diagnoses.py
git commit -m "feat: expose rule-default diagnosis API"
```

## Task 11: Documentation, Full Gates, and Live-Smoke Readiness

**Files:**
- Modify: `.gitignore`
- Modify: `.env.example`
- Modify: `README.md`
- Create: `docs/evaluation/phase2-diagnosis-evaluation.md`
- Modify: `docs/handoffs/2026-07-17-phase2-design-to-plan-handoff.md`

**Interfaces:**
- Produces: reproducible offline commands, safe live command, evaluation interpretation, and final Phase 2 handoff.

- [ ] **Step 1: Write documentation and delivery tests first**

Extend the existing delivery test to assert generated reports are ignored, `.env.example` names but does not populate `DEEPSEEK_API_KEY`, README includes offline evaluation and explicit `--allow-live-api`, and no committed file matches a DeepSeek key pattern.

- [ ] **Step 2: Run RED, then update docs/config**

Run: `.venv\Scripts\python -m pytest tests/test_delivery_config.py -q`
Expected: FAIL for missing Phase 2 documentation, then PASS after edits.

- [ ] **Step 3: Run complete local quality gate**

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m mypy src/afc
```

Expected: all tests PASS, Ruff reports `All checks passed!`, mypy reports no issues.

- [ ] **Step 4: Re-run deterministic evaluation twice**

Run rule evaluation twice and compare SHA-256. Expected: identical artifacts and all hard-gate counts meet design section 10.4.

- [ ] **Step 5: Verify container delivery**

Build the existing digest-pinned Docker image, run Compose health checks, call `/health` and a rule diagnosis request, verify runtime UID/GID `10001:10001`, then clean up Compose services. Expected: build and health checks succeed without adding the API key to image or Compose config.

- [ ] **Step 6: Commit documentation and gate evidence**

```powershell
git add .gitignore .env.example README.md docs/evaluation/phase2-diagnosis-evaluation.md docs/handoffs/2026-07-17-phase2-design-to-plan-handoff.md tests/test_delivery_config.py
git commit -m "docs: document Phase 2 diagnosis evaluation"
```

- [ ] **Step 7: Stop before live API if no key is configured**

Notify the user to set `DEEPSEEK_API_KEY` locally. First run two allowlisted samples. Only after valid schema/evidence/cost output, run the explicit 20-sample live experiment. Never ask the user to paste the key into chat.

## Final Verification

- [ ] Review every design requirement against a task above.
- [ ] Run `git diff main...HEAD --check`.
- [ ] Run full pytest, Ruff, mypy, deterministic evaluation, Docker build, Compose health, API smoke, secret scan, and clean-worktree checks with fresh output.
- [ ] Use `verification-before-completion` before claiming completion.
- [ ] Use `finishing-a-development-branch` to present merge/PR/keep options; do not merge or push without the user's requested integration action.
