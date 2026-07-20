# SpanVouch Phase 5 Multi-Framework Isolated Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible LangGraph/AutoGen execution laboratory and a frozen-trace B0-B5 verification experiment that can test whether isolated verification reduces accepted diagnosis errors without hiding coverage loss.

**Architecture:** Stage A executes matched SupportLab and OpsLab scenarios through a framework-neutral runtime port, then freezes content-addressed `TraceIR` and execution records. Stage B never re-executes an agent; it generates one DeepSeek diagnosis per frozen trace, replays the same diagnosis through B0-B5, joins sealed labels only after provider calls finish, and produces manifest-bound statistical and paper artifacts.

**Tech Stack:** Python `>=3.12,<3.13`, Pydantic 2, LangGraph `>=0.4,<2`, AutoGen AgentChat/Core `>=0.7,<0.8`, OpenTelemetry, httpx, DeepSeek Chat Completions, Qwen3-14B served by a pinned Linux vLLM OpenAI-compatible container, uv, pytest/pytest-asyncio/pytest-cov, Ruff, strict mypy, Docker Compose v2.

## Global Constraints

- Authoritative design: `docs/superpowers/specs/2026-07-19-phase5-multiframework-isolated-verification-design.md`.
- Phase 4 accepted baseline commit is `e2fa68edbf289b032847963be2ccfbe59ee0391a`; Phase 5 planning commit is `096f25f1d325aa9730612177685fe77e5386593a`.
- Keep one `spanvouch` Python distribution and Python `>=3.12,<3.13`.
- Do not modify the six public Contract v1 identities or their frozen JSON schemas.
- `LabScenario`, `RuntimeConfig`, `ExecutionRecord`, corpus records, condition records, and statistics records remain experimental Phase 5 schemas, not Contract v1 roots.
- Production `contracts`, `trace`, `diagnosis`, `verification`, and `review` must never import `spanvouch.labs` or `spanvouch.evaluation`.
- Stage A alone may invoke LangGraph or AutoGen. Stage B may call model endpoints but must never import or execute an agent framework.
- The same `LabScenario`, tool behavior, injection trigger, seed, timeout, step/tool limits, and terminal predicate apply to both frameworks.
- Stage A processes cannot load gold labels, expected findings, mutation metadata, or split identity.
- Stage B provider requests cannot contain gold labels, expected findings, mutation metadata, split identity, framework comparison outcomes, another condition's verdict, credentials, raw provider responses, or hidden reasoning.
- B2 and B3 use the same DeepSeek model identifier, verifier instruction, output schema, sampling configuration, and maximum output-token budget; the intended difference is shared versus isolated provider-visible context.
- B4 is an operational cross-model condition. Do not attribute its effect solely to model identity because its provider and serving stack also change.
- DeepSeek remains the sole formal diagnosis generator. Qwen is a verifier only.
- Pin the Qwen checkpoint revision, vLLM image digest, GPU type, quantization/dtype, chat template, generation configuration, and endpoint behavior in the formal manifest.
- Use `Qwen/Qwen3-14B` in non-thinking mode for the pilot unless the pilot records a typed incompatibility; any replacement creates a new experiment identity and requires design-thread approval.
- vLLM runs on a rented Linux GPU host and is not added to the local Windows project dependency set.
- Default tests, CI, dataset generation, replay, and statistics make zero paid provider calls.
- Every live call requires `--allow-live-provider`, a non-empty experiment ID, and
  `--approved-manifest-sha256` bound to the exact matrix approved before execution;
  formal calls additionally require `--formal-run`.
- Monthly paid budget is CNY 500-1000; pilot paid usage is at most 10% of the approved cap; scheduling new paid work stops at 80% of the cap.
- Cache identity binds trace, diagnosis, condition, prompt version, provider, model, and generation configuration. A cache hit preserves original usage/cost provenance.
- Formal paired cells cannot be selectively dropped or regenerated after model results are observed.
- Pilot uses three repetitions per scenario/framework cell and is excluded from formal primary results.
- Formal repetitions are frozen after pilot power/precision analysis, with a minimum of five and maximum of twenty per scenario/framework cell.
- H1 uses LangGraph as reference; the lower bound of the paired 95% CI for `completion_rate(AutoGen) - completion_rate(LangGraph)` must exceed `-0.05`, and both adapters must reach at least 95% contract-valid execution.
- H2/H3 improvement claims require the upper bound of the paired 95% CI for B3/B4 false-acceptance-risk difference against B2 to be below zero.
- Coverage loss against B2 must stay within the pilot-frozen tolerance and the tolerance cannot exceed ten percentage points.
- Use scenario template as the bootstrap cluster; never treat repetitions as independent templates.
- Report provider, infrastructure, framework execution, framework incompatibility, and contract-invalid failures separately from diagnosis and verification accuracy.
- Null, negative, and unresolved results remain in the claim-evidence ledger.
- Do not implement Conformal risk control, evidence acquisition, CodeLab, Repair Agent, human experiments, broad OOD claims, UI, auth/RBAC, Postgres, Redis, queues, or publication.
- Maintain at least 93% total coverage, Ruff clean, strict mypy clean, wheel build, existing Docker non-root/restart/persistence gates, and secret hygiene.
- Use TDD for every behavior change. If a contract, leakage, hash, statistical, budget, build, or test gate fails, stop the current batch.
- Paid runs and GPU rental are operational actions outside ordinary implementation; obtain explicit user approval immediately before each paid pilot or formal run.

## Verified External API References

Checked on 2026-07-19 before writing this plan:

- AutoGen installation and package split: `https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/installation.html`
- AutoGen custom `BaseChatAgent` lifecycle: `https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/custom-agents.html`
- AutoGen `RoundRobinGroupChat` team API: `https://microsoft.github.io/autogen/stable/reference/python/autogen_agentchat.teams.html`
- LangGraph `StateGraph` graph API: `https://docs.langchain.com/oss/python/langgraph/graph-api`
- vLLM OpenAI-compatible server: `https://docs.vllm.ai/en/stable/serving/openai_compatible_server/`
- vLLM structured JSON output: `https://docs.vllm.ai/en/latest/features/structured_outputs/`
- Official Qwen3 vLLM deployment and non-thinking configuration: `https://github.com/QwenLM/Qwen3/blob/main/docs/source/deployment/vllm.md`
- Official Qwen3-14B model card: `https://huggingface.co/Qwen/Qwen3-14B`

---

## Mandatory Execution Order

```text
1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9
  -> 10 -> 11 -> 12 -> 13 -> 14 -> 15 -> 16 -> 17 -> 18
```

Each Task ends in one reviewable commit. Do not start a Task until the preceding Task's tests and commit have been accepted. Batches are:

- Batch 0: Tasks 1-2
- Batch 1: Tasks 3-6
- Batch 2: Task 7
- Batch 3: Tasks 8-9
- Batch 4: Tasks 10-15
- Batch 5: Tasks 16-18

## Target File Responsibility Map

```text
src/spanvouch/labs/runtime/models.py             experimental runtime values and records
src/spanvouch/labs/runtime/protocols.py          AgentRuntimeAdapter/LabEnvironment ports
src/spanvouch/labs/registry.py                   outer registry joining SupportLab and OpsLab
src/spanvouch/labs/runtime/parity.py             matched-scenario parity validation
src/spanvouch/labs/frameworks/langgraph.py       LangGraph orchestration adapter
src/spanvouch/labs/frameworks/autogen.py         AutoGen custom-agent orchestration adapter
src/spanvouch/labs/supportlab/environment.py     framework-neutral SupportLab behavior
src/spanvouch/labs/supportlab/runtime.py         SupportLab -> LabScenario/environment registry bridge
src/spanvouch/labs/opslab/models.py              OpsLab fault and state models
src/spanvouch/labs/opslab/templates.py           sixteen frozen scenario templates
src/spanvouch/labs/opslab/environment.py         deterministic OpsLab behavior/injection
src/spanvouch/labs/opslab/invariants.py          OpsLab deterministic verification rules
src/spanvouch/evaluation/experiments/config.py   preregistered experiment and budget config
src/spanvouch/evaluation/corpus/models.py         corpus/label manifest research schemas
src/spanvouch/evaluation/corpus/repository.py     content-addressed freeze and verified replay
src/spanvouch/evaluation/corpus/generate.py       Stage A paired execution and corpus freeze
src/spanvouch/evaluation/experiments/diagnosis.py diagnosis request preparation and freezing
src/spanvouch/adapters/models/openai_compatible.py generic Qwen/vLLM provider adapter
src/spanvouch/evaluation/experiments/provider.py  request audit, cache, cost and live-call guards
src/spanvouch/evaluation/experiments/models.py    B0-B5 records and failure taxonomy
src/spanvouch/evaluation/experiments/planner.py   complete paired verification matrix planning
src/spanvouch/evaluation/experiments/conditions.py B0-B5 condition implementations
src/spanvouch/evaluation/experiments/runner.py    replay, post-call join, and result publication
src/spanvouch/evaluation/statistics/metrics.py    risk, coverage, curves and secondary endpoints
src/spanvouch/evaluation/statistics/inference.py  bootstrap, McNemar and Holm procedures
src/spanvouch/evaluation/statistics/claims.py     preregistered H1-H5 claim gates
src/spanvouch/evaluation/paper_assets.py          deterministic tables, CSV and SVG output
```

Existing production files remain focused: `LlmDiagnoser` consumes a new prompt builder, `SemanticVerifier` consumes a new message builder, and neither imports evaluation code.

## Spec Coverage Matrix

| Approved requirement | Tasks |
| --- | --- |
| preregistration, pilot/formal separation, budgets | 1, 12, 18 |
| experimental runtime port and records | 2 |
| framework-neutral SupportLab | 3 |
| LangGraph and AutoGen adapters | 4-5 |
| conformance, parity, H1 inputs | 6 |
| four-family, sixteen-template OpsLab | 7 |
| immutable trace corpus and typed failures | 8-9 |
| fixed DeepSeek diagnoses and B2 context reconstruction | 10 |
| Qwen/vLLM provider and provenance | 11 |
| cache, live-call, cost and leakage controls | 12 |
| B0-B5 complete paired matrix | 13-14 |
| provider-free post-call label join and failure semantics | 15 |
| risk/coverage and statistical inference | 16 |
| paper assets, claim gates, claim ledger | 17 |
| full reproducibility, CI, runbooks and acceptance | 18 |

---

### Task 1: Freeze Phase 5 Experiment Configuration and Preregistration

**Files:**
- Create: `src/spanvouch/evaluation/experiments/__init__.py`
- Create: `src/spanvouch/evaluation/experiments/config.py`
- Create: `tests/evaluation/experiments/test_config.py`
- Create: `evals/configs/phase5-pilot.json`
- Create: `evals/configs/phase5-formal-policy.json`
- Create: `docs/research/phase5-preregistration.md`
- Modify: `docs/research/ivad-claim-evidence-ledger.md`

**Interfaces:**
- Consumes: approved design values and canonical JSON/hash utilities.
- Produces: `ExperimentMode`, `ConditionId`, `ModelEndpointConfig`, `BudgetPolicy`, `Phase5ExperimentConfig`, `FormalFreezePolicy`, `load_experiment_config`, `freeze_formal_config`, and a preregistration whose primary fields cannot change after formal freeze.

- [ ] **Step 1: Write failing configuration tests**

Create tests that instantiate the pilot configuration and assert these exact invariants:

```python
from decimal import Decimal
from pathlib import Path

import pytest

from spanvouch.evaluation.experiments.config import (
    ConditionId,
    ExperimentMode,
    Phase5ExperimentConfig,
    load_experiment_config,
)


def test_checked_in_pilot_configuration_is_complete() -> None:
    config = load_experiment_config(Path("evals/configs/phase5-pilot.json"))
    assert config.mode is ExperimentMode.PILOT
    assert config.repetitions == 3
    assert config.conditions == tuple(ConditionId)
    assert config.generator.provider == "deepseek"
    assert config.cross_model_verifier.model == "Qwen/Qwen3-14B"
    assert config.budget.monthly_cap_cny == Decimal("1000")
    assert config.budget.pilot_fraction == Decimal("0.10")
    assert config.budget.stop_fraction == Decimal("0.80")


def test_formal_config_rejects_unfrozen_primary_fields() -> None:
    payload = load_experiment_config(
        Path("evals/configs/phase5-pilot.json")
    ).model_dump(mode="json")
    payload.update(mode="formal", repetitions=5, frozen_at_utc=None)
    with pytest.raises(ValueError, match="formal configuration must be frozen"):
        Phase5ExperimentConfig.model_validate(payload)
```

- [ ] **Step 2: Run the focused test and confirm the missing-module failure**

Run: `uv run pytest tests/evaluation/experiments/test_config.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: spanvouch.evaluation.experiments`.

- [ ] **Step 3: Implement the frozen configuration models**

Implement immutable Pydantic models with these public values and validators:

```python
class ExperimentMode(StrEnum):
    PILOT = "pilot"
    FORMAL = "formal"


class ConditionId(StrEnum):
    B0 = "b0_no_verifier"
    B1 = "b1_deterministic"
    B2 = "b2_deepseek_shared"
    B3 = "b3_deepseek_isolated"
    B4 = "b4_qwen_isolated"
    B5 = "b5_deterministic_qwen"


class ModelEndpointConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    endpoint_class: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    max_tokens: int = Field(ge=1, le=4096)
    temperature: float = Field(ge=0.0, le=2.0)
    extra_body: dict[str, JsonValue] = Field(default_factory=dict)


class BudgetPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    monthly_cap_cny: Decimal = Field(gt=0)
    pilot_fraction: Decimal = Field(gt=0, le=Decimal("0.10"))
    stop_fraction: Decimal = Field(gt=0, le=Decimal("0.80"))


class Phase5ExperimentConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    experiment_id: str = Field(pattern=r"^phase5-[a-z0-9-]+$")
    mode: ExperimentMode
    seed: int
    repetitions: int = Field(ge=3, le=20)
    conditions: tuple[ConditionId, ...]
    frameworks: tuple[Literal["langgraph", "autogen"], ...]
    generator: ModelEndpointConfig
    shared_verifier: ModelEndpointConfig
    isolated_verifier: ModelEndpointConfig
    cross_model_verifier: ModelEndpointConfig
    budget: BudgetPolicy
    coverage_loss_tolerance: float | None = Field(default=None, ge=0.0, le=0.10)
    frozen_at_utc: datetime | None = None
    config_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
```

Validators must require all six conditions exactly once and both frameworks exactly once. Pilot requires exactly three repetitions and no coverage tolerance. Formal requires 5-20 repetitions, UTC `frozen_at_utc`, a coverage tolerance, and `config_sha256` equal to the canonical hash with the hash field removed.

- [ ] **Step 4: Add checked-in pilot/formal configurations and preregistration**

The pilot JSON fixes seed `20260719`, six conditions, both frameworks, DeepSeek generation/verification, Qwen3-14B with `chat_template_kwargs.enable_thinking=false`, CNY 1000 cap, 10% pilot fraction, and 80% stop fraction. The formal policy JSON fixes minimum repetitions 5, maximum repetitions 20, maximum coverage loss 0.10, required confidence level 0.95, bootstrap draws 10,000, and Holm correction. `freeze_formal_config` requires concrete pilot-analysis inputs and emits a fully valid, self-hashed formal config; it never emits a partially filled config.

The preregistration records RQ1-RQ5, H1-H5, primary/secondary endpoints, scenario-template clustering, paired bootstrap, McNemar secondary analysis, Holm correction, exclusions, missingness, coverage-tolerance selection, pilot exclusion, and the rule that null results remain visible. Add Phase 5 rows to the claim ledger with status `planned; no Phase 5 evidence yet`.

- [ ] **Step 5: Run tests and static checks**

Run: `uv run pytest tests/evaluation/experiments/test_config.py -v`

Expected: PASS.

Run: `uv run ruff check src/spanvouch/evaluation/experiments tests/evaluation/experiments && uv run mypy`

Expected: both commands exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/spanvouch/evaluation/experiments tests/evaluation/experiments evals/configs docs/research
git commit -m "docs: freeze Phase 5 experiment protocol"
```

### Task 2: Add Experimental Runtime Models and Ports

**Files:**
- Create: `src/spanvouch/labs/runtime/__init__.py`
- Create: `src/spanvouch/labs/runtime/models.py`
- Create: `src/spanvouch/labs/runtime/protocols.py`
- Create: `tests/labs/runtime/test_models.py`
- Create: `tests/labs/runtime/test_protocols.py`
- Modify: `tests/architecture/test_dependency_direction.py`

**Interfaces:**
- Consumes: `TraceIR`, canonical hashing, Pydantic research values.
- Produces: `FrameworkId`, `ExecutionStatus`, `RuntimeFailureCategory`, `RuntimeConfig`, `LabScenario`, `AgentAction`, `ToolObservation`, `RuntimeState`, `RuntimeFailure`, `ExecutionProvenance`, `ExecutionRecord`, `ExecutionRecord.from_run`, `LabEnvironment`, `LabEnvironmentRegistry`, and `AgentRuntimeAdapter.execute`.

- [ ] **Step 1: Write failing model and boundary tests**

Test immutability, unknown-field rejection, canonical trace/config hashes, disjoint failure categories, no secret/raw/gold field names in serialized records, and the exact runtime protocol:

```python
from typing import get_type_hints

from spanvouch.labs.runtime.models import ExecutionRecord, LabScenario, RuntimeConfig
from spanvouch.labs.runtime.protocols import AgentRuntimeAdapter


def test_runtime_adapter_has_the_frozen_port() -> None:
    hints = get_type_hints(AgentRuntimeAdapter.execute)
    assert hints["scenario"] is LabScenario
    assert hints["run_config"] is RuntimeConfig
    assert hints["return"] == ExecutionRecord


def test_execution_record_excludes_evaluator_and_secret_fields(record: ExecutionRecord) -> None:
    serialized = record.model_dump_json()
    for forbidden in (
        "gold_label",
        "expected_finding",
        "split_identity",
        "api_key",
        "authorization",
        "raw_response",
        "prompt_text",
        "hidden_reasoning",
    ):
        assert forbidden not in serialized.lower()
```

Extend the architecture test so production core roots still cannot import the new runtime, framework, OpsLab, corpus, experiment, or statistics packages.

- [ ] **Step 2: Run focused tests and confirm the missing-module failure**

Run: `uv run pytest tests/labs/runtime tests/architecture/test_dependency_direction.py -v`

Expected: FAIL during collection because `spanvouch.labs.runtime` does not exist.

- [ ] **Step 3: Implement immutable runtime values**

Use the following exact public enums and fields:

```python
class FrameworkId(StrEnum):
    LANGGRAPH = "langgraph"
    AUTOGEN = "autogen"


class ExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STEP_LIMIT = "step_limit"
    INCOMPATIBLE = "incompatible"


class RuntimeFailureCategory(StrEnum):
    FRAMEWORK_EXECUTION = "framework_execution_failure"
    FRAMEWORK_INCOMPATIBILITY = "framework_incompatibility"
    INFRASTRUCTURE = "infrastructure_failure"


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    seed: int
    repetition: int = Field(ge=1)
    max_steps: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    max_tool_calls: int = Field(ge=1)


class LabScenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scenario_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    domain: Literal["supportlab", "opslab"]
    failure_family: str = Field(min_length=1)
    user_request: str = Field(min_length=1)
    parameters: dict[str, JsonValue]
    injection: dict[str, JsonValue]
    tool_contract_sha256: str = Field(pattern=SHA256_PATTERN)
    terminal_predicate_id: str = Field(min_length=1)
    allowed_evidence_selectors: tuple[str, ...]
```

`AgentAction` has `kind: Literal["tool", "final"]`, optional tool name, JSON arguments, and optional final message with a validator enforcing the matching shape. `ToolObservation` records tool name, canonical result/error, status, and retryability. `RuntimeState` records step, tool-call count, observations, and terminal fields, and provides immutable `initial()`, `with_observation(...)`, `with_final(...)`, and `with_failure(...)` constructors used by both adapters.

`ExecutionProvenance` contains Git commit, package version, dependency-lock hash, dataset-manifest hash, environment hash, sorted tool/runtime versions and dirty-worktree flag. `ExecutionRecord` contains one `TraceIR` plus its canonical hash, framework/version, scenario/template/domain/family, seed, repetition, config hash, `status`, typed optional failure, UTC timings, latency, step/tool counts, final message, and `ExecutionProvenance`. Validate trace hash, runtime config hash, nonnegative counts, sorted provenance maps, UTC timestamps, and status/failure consistency. `ExecutionRecord.from_run(...)` is the only adapter-facing constructor and computes hashes/count/timing fields from validated inputs.

The constructor signature is fixed for both adapters:

```python
@classmethod
def from_run(
    cls,
    *,
    scenario: LabScenario,
    run_config: RuntimeConfig,
    framework_id: FrameworkId,
    framework_version: str,
    trace: TraceIR,
    state: RuntimeState,
    status: ExecutionStatus,
    failure: RuntimeFailure | None,
    started_at: datetime,
    completed_at: datetime,
    provenance: ExecutionProvenance,
) -> Self: ...
```

`RuntimeFailure` contains category, stable code, retryable flag, and a SHA-256 of the sanitized error message; it never persists a raw exception body. Even an early framework failure produces a minimal valid root failure trace so every record retains the same trace/hash shape.

- [ ] **Step 4: Define the runtime protocols**

```python
class LabEnvironment(Protocol):
    scenario: LabScenario

    async def decide(self, state: RuntimeState) -> AgentAction: ...
    async def execute(self, action: AgentAction) -> ToolObservation: ...
    def terminal_status(self, state: RuntimeState) -> ExecutionStatus | None: ...


class LabEnvironmentRegistry(Protocol):
    def build(self, scenario: LabScenario) -> LabEnvironment: ...


class AgentRuntimeAdapter(Protocol):
    framework_id: FrameworkId
    framework_version: str

    async def execute(
        self,
        scenario: LabScenario,
        run_config: RuntimeConfig,
    ) -> ExecutionRecord: ...
```

Keep every import in `labs/runtime` limited to stdlib, Pydantic, `contracts`, and canonical versioning utilities. It cannot import LangGraph, AutoGen, SupportLab, OpsLab, evaluation, providers, or labels.

- [ ] **Step 5: Run focused tests and architecture checks**

Run: `uv run pytest tests/labs/runtime tests/architecture/test_dependency_direction.py -v`

Expected: PASS.

Run: `uv run ruff check src/spanvouch/labs/runtime tests/labs/runtime && uv run mypy`

Expected: both commands exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/spanvouch/labs/runtime tests/labs/runtime tests/architecture/test_dependency_direction.py
git commit -m "feat: define Phase 5 runtime port"
```

### Task 3: Extract a Framework-Neutral SupportLab Environment

**Files:**
- Create: `src/spanvouch/labs/supportlab/environment.py`
- Create: `src/spanvouch/labs/supportlab/runtime.py`
- Create: `tests/labs/supportlab/test_environment.py`
- Create: `tests/labs/supportlab/test_runtime_bridge.py`
- Modify: `src/spanvouch/labs/supportlab/graph.py`
- Modify: `tests/labs/supportlab/test_graph.py`

**Interfaces:**
- Consumes: existing `Scenario`, `ScriptedDecisionModel`, `SupportTools`, repository/policy behavior, runtime ports.
- Produces: `SupportLabEnvironment`, `SupportLabEnvironmentRegistry`, `support_scenario_to_lab`, `build_support_lab_scenarios`, and a compatibility `run_support_scenario` whose Phase 3 observable result stays unchanged.

- [ ] **Step 1: Characterize existing behavior before extraction**

Add parameterized tests for all 20 `build_scenarios()` values. Capture scenario ID, outcome, steps, observations, final message, tool names, tool arguments, tool errors, root status, and `TraceIR`. Assert the clean/fault counts remain four clean plus two for each of eight failure types.

Run: `uv run pytest tests/labs/supportlab/test_graph.py -v`

Expected: PASS against the accepted implementation before moving behavior.

- [ ] **Step 2: Write failing framework-neutral environment tests**

```python
@pytest.mark.asyncio
async def test_environment_runs_without_importing_a_framework() -> None:
    scenario = next(item for item in build_scenarios() if item.scenario_id == "clean-01")
    lab_scenario = support_scenario_to_lab(scenario)
    environment = SupportLabEnvironmentRegistry().build(lab_scenario)
    state = RuntimeState.initial()

    while environment.terminal_status(state) is None:
        action = await environment.decide(state)
        if action.kind == "final":
            state = state.with_final(action.final_message or "")
        else:
            observation = await environment.execute(action)
            state = state.with_observation(observation)

    assert environment.terminal_status(state) is ExecutionStatus.SUCCEEDED
    assert state.tool_calls == 5
```

The test file must not import `langgraph` or `autogen_agentchat`.

- [ ] **Step 3: Move domain decisions and tool dispatch into `SupportLabEnvironment`**

`SupportLabEnvironment.decide` delegates to `ScriptedDecisionModel.next_decision` and converts `AgentDecision` to `AgentAction`. `execute` owns the existing five-tool dispatch, `Decimal` conversion, approval conversion, ignored-error semantics, and sanitized `ToolObservation`. `terminal_status` maps final, hard tool error, and step-limit state without reading framework state.

`support_scenario_to_lab` serializes only execution inputs/injection fields and computes a stable tool-contract hash from the five tool names plus argument keys. `build_support_lab_scenarios(seed)` returns execution-only `LabScenario` values without importing or retaining `expected_failure` or `expected_critical_operation`. The accepted `build_scenarios`/`Scenario` API remains for historical Phase 3 dataset compatibility, but the Phase 5 Stage A generator is forbidden from importing it. `SupportLabEnvironmentRegistry.build` rejects non-SupportLab domains and unknown scenario IDs with a typed framework incompatibility.

- [ ] **Step 4: Turn the existing graph function into a compatibility wrapper**

Keep the public signature and `SupportRunResult`. The wrapper constructs `SupportLabEnvironment` and delegates orchestration to a private legacy LangGraph path until Task 4 replaces it with `LangGraphRuntimeAdapter`; do not duplicate tool semantics in `graph.py`.

- [ ] **Step 5: Run behavior, import, and coverage tests**

Run: `uv run pytest tests/labs/supportlab tests/invariants/test_supportlab_rules.py -v`

Expected: PASS with all prior scenario outcomes unchanged.

Run: `uv run pytest tests/labs/supportlab --cov=spanvouch.labs.supportlab --cov-report=term-missing`

Expected: PASS and no untested branch introduced in `environment.py`.

- [ ] **Step 6: Commit**

```bash
git add src/spanvouch/labs/supportlab tests/labs/supportlab
git commit -m "refactor: extract SupportLab environment"
```

### Task 4: Implement the LangGraph Runtime Adapter

**Files:**
- Create: `src/spanvouch/labs/frameworks/__init__.py`
- Create: `src/spanvouch/labs/frameworks/langgraph.py`
- Create: `tests/labs/frameworks/test_langgraph_adapter.py`
- Modify: `src/spanvouch/observability/tracing.py`
- Modify: `src/spanvouch/labs/supportlab/graph.py`
- Modify: `tests/labs/supportlab/test_graph.py`

**Interfaces:**
- Consumes: `LabEnvironmentRegistry`, `ExecutionProvenance`, `LabScenario`, `RuntimeConfig`, OpenTelemetry and LangGraph `StateGraph`.
- Produces: `LangGraphRuntimeAdapter.execute` and `build_run_tracer(service_name)`; the old SupportLab graph API becomes a thin result-mapping wrapper.

- [ ] **Step 1: Write the failing adapter test**

```python
@pytest.mark.asyncio
async def test_langgraph_adapter_returns_a_hashed_contract_valid_record(
    execution_provenance: ExecutionProvenance,
) -> None:
    scenario = support_scenario_to_lab(
        next(item for item in build_scenarios() if item.scenario_id == "clean-01")
    )
    adapter = LangGraphRuntimeAdapter(
        SupportLabEnvironmentRegistry(), provenance=execution_provenance
    )
    record = await adapter.execute(
        scenario,
        RuntimeConfig(
            seed=20260719,
            repetition=1,
            max_steps=8,
            timeout_seconds=5.0,
            max_retries=0,
            max_tool_calls=8,
        ),
    )

    assert record.framework_id is FrameworkId.LANGGRAPH
    assert record.status is ExecutionStatus.SUCCEEDED
    assert record.tool_calls == 5
    assert record.trace_sha256 == canonical_sha256(record.trace)
    assert record.trace.spans[0].name == "supportlab.run"
```

Add cases for timeout, step limit, unknown tool, ignored tool error, cancellation, and environment incompatibility. Assert each maps to exactly one typed `RuntimeFailureCategory`.

- [ ] **Step 2: Run the test and confirm the missing-adapter failure**

Run: `uv run pytest tests/labs/frameworks/test_langgraph_adapter.py -v`

Expected: FAIL during collection because `spanvouch.labs.frameworks.langgraph` does not exist.

- [ ] **Step 3: Generalize the in-memory tracer factory**

Replace the SupportLab-only factory with:

```python
def build_run_tracer(service_name: str) -> tuple[Tracer, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer(service_name), exporter


def build_test_tracer() -> tuple[Tracer, InMemorySpanExporter]:
    return build_run_tracer("spanvouch.labs.supportlab")
```

The compatibility function preserves all existing callers.

- [ ] **Step 4: Implement LangGraph orchestration around the environment**

`LangGraphRuntimeAdapter` must:

1. build a fresh environment per run;
2. create one root AGENT span and one WORKFLOW span per decision transition;
3. use a `TypedDict` state containing only `RuntimeState` fields;
4. route `decide -> execute -> decide` and both terminal paths to `END`;
5. enforce wall-clock timeout with `asyncio.timeout` and check step/tool limits before scheduling a node;
6. map exported spans with `map_spans`;
7. return `ExecutionRecord.from_run(...)` for success or typed failure;
8. obtain `framework_version` from `importlib.metadata.version("langgraph")`;
9. never read evaluator modules or labels.

Use `try/finally` so spans are ended and exporter state is consumed even on timeout/cancellation. Do not retry a full scenario inside the adapter.

- [ ] **Step 5: Delegate the old graph API to the adapter-compatible orchestration**

`run_support_scenario` keeps its accepted signature and maps the new record to `SupportRunResult`; the caller-provided tracer path remains available for Phase 3 tests. Remove the second copy of decide/execute semantics from `graph.py`.

- [ ] **Step 6: Run regression and static checks**

Run: `uv run pytest tests/labs/frameworks/test_langgraph_adapter.py tests/labs/supportlab tests/invariants -v`

Expected: PASS with all 20 SupportLab outcomes unchanged.

Run: `uv run ruff check src/spanvouch/labs/frameworks src/spanvouch/observability tests/labs && uv run mypy`

Expected: both commands exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/spanvouch/labs/frameworks src/spanvouch/labs/supportlab/graph.py src/spanvouch/observability/tracing.py tests/labs
git commit -m "feat: add LangGraph lab runtime adapter"
```

### Task 5: Add AutoGen Dependencies and Native Team Runtime Adapter

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/spanvouch/labs/frameworks/autogen.py`
- Create: `tests/labs/frameworks/test_autogen_adapter.py`
- Modify: `tests/test_delivery_config.py`

**Interfaces:**
- Consumes: AutoGen AgentChat `BaseChatAgent`, `Response`, `TextMessage`, `RoundRobinGroupChat`, `FunctionalTermination`, `MaxMessageTermination`, Core `CancellationToken`, and the same environment/provenance values as Task 4.
- Produces: `EnvironmentActionAgent`, `EnvironmentToolAgent`, native team orchestration, and `AutoGenRuntimeAdapter.execute` without adding an AutoGen model client or provider dependency.

- [ ] **Step 1: Write delivery and adapter tests before adding dependencies**

Assert `pyproject.toml` declares both direct imports with bounded versions:

```python
def test_autogen_dependencies_are_bounded() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert "autogen-agentchat>=0.7,<0.8" in project["dependencies"]
    assert "autogen-core>=0.7,<0.8" in project["dependencies"]
```

Add the same clean, fault, step-limit, timeout, cancellation, and incompatibility cases used for LangGraph. Assert `framework_id=autogen`, exact installed version provenance, a single AGENT root, and no provider calls.

- [ ] **Step 2: Run tests and confirm dependency/import failures**

Run: `uv run pytest tests/test_delivery_config.py tests/labs/frameworks/test_autogen_adapter.py -v`

Expected: FAIL because the bounded dependencies and adapter do not exist.

- [ ] **Step 3: Add and lock the minimal AutoGen dependency set**

Add exactly:

```toml
"autogen-agentchat>=0.7,<0.8",
"autogen-core>=0.7,<0.8",
```

Run: `uv lock`

Expected: `uv.lock` resolves one compatible 0.7.x pair. Do not add `autogen-ext`, OpenAI SDK, Docker executor, Studio, or Magentic-One packages.

- [ ] **Step 4: Implement the deterministic AutoGen team agents**

The two custom agents share one run-local session while AutoGen owns message rotation and termination:

```python
@dataclass
class AutoGenLabSession:
    environment: LabEnvironment
    state: RuntimeState = field(default_factory=RuntimeState.initial)


class EnvironmentActionAgent(BaseChatAgent):
    def __init__(self, session: AutoGenLabSession) -> None:
        super().__init__(name="spanvouch_lab_agent", description="Executes one lab decision")
        self._session = session

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

    async def on_messages(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> Response:
        if cancellation_token.is_cancelled():
            raise asyncio.CancelledError
        action = await self._session.environment.decide(self._session.state)
        return Response(
            chat_message=TextMessage(
                content=canonical_json(action.model_dump(mode="json")),
                source=self.name,
            )
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        if cancellation_token.is_cancelled():
            raise asyncio.CancelledError
        self._session.state = RuntimeState.initial()
```

`EnvironmentToolAgent` is another `BaseChatAgent`. It validates the latest `TextMessage` as `AgentAction`, executes tool actions through `session.environment`, updates `session.state`, and returns canonical `ToolObservation`; a final action is never sent to the tool agent.

Create `RoundRobinGroupChat([action_agent, tool_agent], ...)` with `FunctionalTermination` that stops on either a parsed final `AgentAction` or a non-null `session.environment.terminal_status(session.state)`, OR-ed with `MaxMessageTermination(2 * max_steps + 1)`. The adapter calls `team.run(task=TextMessage(...), cancellation_token=token)` once. No `AssistantAgent`, selector model, model client or external provider is permitted in Stage A. Create a fresh session, both agents, termination objects and team per run because AutoGen components are stateful and not coroutine-safe.

- [ ] **Step 5: Implement `AutoGenRuntimeAdapter.execute`**

Mirror LangGraph limits, tracing, record construction, and typed error mapping. Use `CancellationToken` plus `asyncio.timeout`; on timeout call `token.cancel()` and await cancellation. In `finally`, reset the team when safe and call `close()` on both custom agents. Obtain `framework_version` from `importlib.metadata.version("autogen-agentchat")`; do not expose AutoGen message/team objects outside this module.

- [ ] **Step 6: Run adapter, package, and lock checks**

Run: `uv sync --all-groups && uv run pytest tests/test_delivery_config.py tests/labs/frameworks -v`

Expected: PASS; the tests make zero network/model calls.

Run: `uv run ruff check src tests && uv run mypy`

Expected: both commands exit 0.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/spanvouch/labs/frameworks/autogen.py tests/labs/frameworks/test_autogen_adapter.py tests/test_delivery_config.py
git commit -m "feat: add AutoGen lab runtime adapter"
```

### Task 6: Enforce Runtime Conformance and Scenario Parity

**Files:**
- Create: `src/spanvouch/labs/runtime/parity.py`
- Create: `tests/labs/runtime/test_adapter_conformance.py`
- Create: `tests/labs/runtime/test_parity.py`
- Modify: `src/spanvouch/labs/runtime/models.py`
- Modify: `src/spanvouch/labs/runtime/__init__.py`

**Interfaces:**
- Consumes: both runtime adapters and paired `ExecutionRecord` values.
- Produces: `ParityDimension`, `ParityMismatch`, `ParityResult`, `ScenarioParityValidator.validate`, and one shared conformance suite.

- [ ] **Step 1: Write the shared adapter conformance suite**

Parameterize one test suite over LangGraph and AutoGen factories. It must assert:

- clean and all eight SupportLab faults execute;
- config seed/hash, scenario identity and trace hash are correct;
- step/tool/time limits are enforced;
- exceptions become typed records rather than escaping;
- cancellation propagates only `CancelledError` after cleanup;
- a second run has no state from the first;
- serialized records contain no forbidden evaluator/provider fields.

Run: `uv run pytest tests/labs/runtime/test_adapter_conformance.py -v`

Expected: PASS for LangGraph and expose any AutoGen lifecycle mismatch before parity work.

- [ ] **Step 2: Write failing parity-validator tests**

```python
def test_parity_accepts_matched_framework_records(
    langgraph_record: ExecutionRecord,
    autogen_record: ExecutionRecord,
) -> None:
    result = ScenarioParityValidator().validate(langgraph_record, autogen_record)
    assert result.is_match is True
    assert result.mismatches == ()


def test_parity_reports_tool_argument_drift(
    langgraph_record: ExecutionRecord,
    autogen_record: ExecutionRecord,
) -> None:
    changed = with_changed_tool_argument(autogen_record, "order_id", "wrong-order")
    result = ScenarioParityValidator().validate(langgraph_record, changed)
    assert result.is_match is False
    assert result.mismatches[0].dimension is ParityDimension.TOOL_ARGUMENTS
```

- [ ] **Step 3: Implement explicit parity dimensions**

`ParityDimension` contains `SCENARIO_INPUT`, `TOOL_SEQUENCE`, `TOOL_ARGUMENTS`, `TOOL_RESULTS`, `INJECTION_TRIGGER`, `RUNTIME_LIMIT`, `TERMINAL_PREDICATE`, `OUTCOME`, and `EVIDENCE_SELECTOR`. A mismatch stores only canonical hashes and safe identifiers, never raw secrets/tool bodies.

The validator compares normalized traces after removing framework name/version, timestamps, trace/span IDs, and framework-only WORKFLOW spans. It must not normalize tool name, arguments, result/error, status, ordering, injection markers, or terminal outcome.

- [ ] **Step 4: Add a typed approved-incompatibility path**

`ParityResult` may be `matched`, `mismatched`, or `incompatible`. `incompatible` requires both a `framework_incompatibility` record and a stable incompatibility code. No caller can turn an ordinary mismatch into an exclusion string.

- [ ] **Step 5: Run both adapters against the complete SupportLab**

Run: `uv run pytest tests/labs/runtime tests/labs/frameworks tests/labs/supportlab -v`

Expected: PASS for 20 paired scenarios and all injected mismatch tests.

- [ ] **Step 6: Commit**

```bash
git add src/spanvouch/labs/runtime tests/labs/runtime
git commit -m "feat: enforce multi-framework scenario parity"
```

### Task 7: Build the Sixteen-Template OpsLab Pilot

**Files:**
- Create: `src/spanvouch/labs/opslab/__init__.py`
- Create: `src/spanvouch/labs/opslab/models.py`
- Create: `src/spanvouch/labs/opslab/templates.py`
- Create: `src/spanvouch/labs/opslab/environment.py`
- Create: `src/spanvouch/labs/opslab/invariants.py`
- Create: `src/spanvouch/labs/registry.py`
- Create: `tests/labs/opslab/test_templates.py`
- Create: `tests/labs/opslab/test_environment.py`
- Create: `tests/labs/opslab/test_invariants.py`
- Create: `tests/labs/opslab/test_framework_parity.py`

**Interfaces:**
- Consumes: runtime ports, deterministic clocks/counters, invariant rule interfaces, both framework adapters.
- Produces: `OpsFailureFamily`, `OpsFaultProfile`, `OpsScenarioTemplate`, `build_opslab_templates`, `OpsLabEnvironment`, `CombinedLabEnvironmentRegistry`, and `opslab_rules`.

- [ ] **Step 1: Write the frozen template inventory test**

Require these exact IDs and one control per family:

```python
EXPECTED = {
    "timeout": {
        "timeout-no-retry",
        "timeout-unbounded-retry",
        "retry-amplification",
        "timeout-control",
    },
    "resource": {
        "rate-limit-unhandled",
        "resource-exhaustion",
        "degradation-missing",
        "resource-control",
    },
    "concurrency": {
        "lease-expiry",
        "lock-contention",
        "deadlock-cycle",
        "concurrency-control",
    },
    "recovery": {
        "checkpoint-stale",
        "resume-duplicate",
        "workflow-state-drift",
        "recovery-control",
    },
}


def test_opslab_has_four_families_and_sixteen_templates() -> None:
    templates = build_opslab_templates()
    assert len(templates) == 16
    grouped = {
        family: {item.template_id for item in templates if item.family.value == family}
        for family in EXPECTED
    }
    assert grouped == EXPECTED
    assert sum(item.injection is None for item in templates) == 4
```

- [ ] **Step 2: Run the inventory test and confirm the missing-package failure**

Run: `uv run pytest tests/labs/opslab/test_templates.py -v`

Expected: FAIL during collection because `spanvouch.labs.opslab` does not exist.

- [ ] **Step 3: Implement deterministic templates and state models**

Each `OpsScenarioTemplate` has template ID, family, user request, ordered operation plan, optional injection trigger `(operation, attempt)`, terminal predicate ID and required evidence selectors. A template with no injection executes the matched healthy path, but no `is_control`/expected field exists in Stage A values. No gold/expected type exists anywhere under `spanvouch.labs.opslab`; the separate labels process in Task 9 owns control status and expected diagnosis mapping. `to_lab_scenario` serializes only the injection necessary to execute the lab.

Use a logical integer clock, deterministic token bucket, ordered lock/lease table, and versioned checkpoint store. Do not use real sleeps, OS locks, wall-clock races, random network calls, or external services in unit/formal generation.

- [ ] **Step 4: Implement all failure semantics and controls**

The environment must expose observable evidence for:

- timeout: deadline, attempts, retry policy, backoff, upstream calls;
- resource: capacity, remaining tokens, rejection, fallback/degradation result;
- concurrency: owner, lease version/expiry, wait-for edges, acquisition result;
- recovery: checkpoint version, operation idempotency key, replay count, state hashes.

Controls run the same operation shape without the fault and terminate successfully. Fault templates terminate with a deterministic failed/step-limit outcome appropriate to the injected behavior.

- [ ] **Step 5: Implement OpsLab deterministic rules**

Create one rule per required evidence/behavior family plus a final-state rule. Rules return existing invariant results and stable finding codes; they must distinguish the three faults from the no-failure control in each family and never import framework adapters.

- [ ] **Step 6: Add the combined domain registry and paired framework test**

`CombinedLabEnvironmentRegistry` lives in `labs/registry.py`, delegates `domain="supportlab"` and `domain="opslab"` to their registries, and returns typed incompatibility for any other domain. This outer registry prevents `labs/runtime` and SupportLab from importing OpsLab. Run all 16 templates through both adapters with identical configs and require `ScenarioParityValidator` to match.

- [ ] **Step 7: Run the complete lab suite**

Run: `uv run pytest tests/labs tests/invariants -v`

Expected: PASS for 20 SupportLab and 16 OpsLab scenarios in both frameworks.

Run: `uv run ruff check src/spanvouch/labs tests/labs && uv run mypy`

Expected: both commands exit 0.

- [ ] **Step 8: Commit**

```bash
git add src/spanvouch/labs/opslab src/spanvouch/labs/registry.py tests/labs/opslab
git commit -m "feat: add deterministic OpsLab pilot"
```

### Task 8: Implement the Content-Addressed Trace Replay Repository

**Files:**
- Create: `src/spanvouch/evaluation/corpus/__init__.py`
- Create: `src/spanvouch/evaluation/corpus/models.py`
- Create: `src/spanvouch/evaluation/corpus/repository.py`
- Create: `tests/evaluation/corpus/test_models.py`
- Create: `tests/evaluation/corpus/test_repository.py`
- Create: `tests/evaluation/corpus/conftest.py`
- Modify: `src/spanvouch/evaluation/artifacts.py`

**Interfaces:**
- Consumes: `ExecutionRecord`, `TraceIR`, canonical JSON/hash utilities and safe artifact publishing primitives.
- Produces: `CorpusCell`, `CorpusEntry`, `CorpusManifestMetadata`, `CorpusManifest.from_entries`, `TraceReplayRepository.freeze`, `TraceReplayRepository.load`, and `TraceReplayRepository.verify`.

- [ ] **Step 1: Write failing corpus model tests**

```python
def test_corpus_entry_binds_record_and_trace_hashes(record: ExecutionRecord) -> None:
    entry = CorpusEntry.from_record(record)
    assert entry.record_sha256 == canonical_sha256(record)
    assert entry.trace_sha256 == canonical_sha256(record.trace)
    assert entry.cell == CorpusCell(
        domain=record.domain,
        template_id=record.template_id,
        scenario_id=record.scenario_id,
        framework_id=record.framework_id,
        repetition=record.repetition,
        seed=record.seed,
    )


def test_manifest_rejects_duplicate_cells(
    entry: CorpusEntry,
    manifest_metadata: CorpusManifestMetadata,
) -> None:
    with pytest.raises(ValueError, match="corpus cells must be unique"):
        CorpusManifest.from_entries(entries=(entry, entry), metadata=manifest_metadata)
```

The models must reject unsorted entries, duplicate cells, unknown payload paths, path traversal, hash mismatch, mutable timestamps, and any evaluator-only field.

- [ ] **Step 2: Write failing repository tests**

Cover atomic first publication, second-write rejection, missing payload, changed byte, unknown payload, symlink/reparse-point payload, duplicate content under a second name, manifest mismatch, verified replay, and cleanup after a failed staged publish.

Run: `uv run pytest tests/evaluation/corpus -v`

Expected: FAIL during collection because `spanvouch.evaluation.corpus` does not exist.

- [ ] **Step 3: Implement research corpus models**

Use this storage identity:

```text
$CORPUS_ROOT/
  manifest.json
  records/sha256/$RECORD_SHA256.json
  traces/sha256/$TRACE_SHA256.json
```

`CorpusCell` contains `domain`, `template_id`, `scenario_id`, `framework_id`, `repetition`, and `seed`. `CorpusEntry` binds one cell to record and trace hashes/paths plus execution status. `CorpusManifestMetadata` contains corpus ID, pilot/formal mode, experiment config hash, code/lock/dataset provenance, creation UTC and parity-result hash. `CorpusManifest.from_entries` sorts/validates entries and derives all payload hashes; callers cannot supply derived hashes independently.

- [ ] **Step 4: Implement no-replace freeze and verified replay**

`freeze(records, parity_results, destination, manifest_metadata)` must:

1. validate every `ExecutionRecord` before writing;
2. canonicalize each record and trace;
3. stage under a sibling temporary directory;
4. classify every payload for secrets;
5. write content-addressed names and sorted manifest;
6. fsync files/directories where supported;
7. publish with existing no-replace semantics;
8. delete only the owned staging tree after failure.

`load(cell)` first runs `verify()`, rehashes bytes, validates both models, checks record/trace equality, and returns the immutable record. A repository instance opened for formal replay is read-only.

- [ ] **Step 5: Run corpus and artifact regression tests**

Run: `uv run pytest tests/evaluation/corpus tests/evaluation/test_artifacts.py tests/evaluation/test_provenance.py -v`

Expected: PASS on Windows path/ownership cases and portable paths.

Run: `uv run ruff check src/spanvouch/evaluation/corpus tests/evaluation/corpus && uv run mypy`

Expected: both commands exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/spanvouch/evaluation/corpus src/spanvouch/evaluation/artifacts.py tests/evaluation/corpus
git commit -m "feat: add immutable trace replay corpus"
```

### Task 9: Generate the Paired Stage A Corpus and Sealed Labels

**Files:**
- Create: `src/spanvouch/evaluation/corpus/generate.py`
- Create: `src/spanvouch/evaluation/corpus/labels.py`
- Create: `src/spanvouch/evaluation/corpus/gold_specs.py`
- Create: `src/spanvouch/evaluation/run_phase5_corpus.py`
- Create: `src/spanvouch/evaluation/generate_phase5_labels.py`
- Create: `tests/evaluation/corpus/test_generate.py`
- Create: `tests/evaluation/corpus/test_label_isolation.py`
- Create: `tests/evaluation/test_run_phase5_corpus.py`
- Modify: `tests/architecture/test_dependency_direction.py`
- Modify: `src/spanvouch/cli/main.py`
- Modify: `tests/cli/test_main.py`

**Interfaces:**
- Consumes: checked-in experiment config, both adapters, SupportLab/OpsLab inventories, parity validator and replay repository.
- Produces: `build_corpus_plan`, `generate_phase5_corpus`, `GoldLabelManifest`, `generate_phase5_labels`, and CLI commands `spanvouch labs corpus` / `spanvouch labs labels`.

- [ ] **Step 1: Write the corpus-plan test**

For pilot config, assert one cell for every scenario/template x framework x three repetitions, stable ordering, paired seeds, and no condition/model fields. The exact expected cell count is:

```python
SUPPORTLAB_SCENARIOS = 20
OPSLAB_TEMPLATES = 16
FRAMEWORKS = 2
PILOT_REPETITIONS = 3
EXPECTED_PILOT_CELLS = (
    (SUPPORTLAB_SCENARIOS + OPSLAB_TEMPLATES) * FRAMEWORKS * PILOT_REPETITIONS
)


def test_pilot_corpus_plan_is_complete(config: Phase5ExperimentConfig) -> None:
    plan = build_corpus_plan(config)
    assert len(plan) == EXPECTED_PILOT_CELLS == 216
    assert len({cell.identity for cell in plan}) == 216
```

- [ ] **Step 2: Write a real import-isolation test**

Parse `generate.py` and `run_phase5_corpus.py` with `ast`. Reject imports from `corpus.labels`, `corpus.gold_specs`, legacy `supportlab.scenarios.build_scenarios`, diagnosis/review labels, experiment conditions/statistics, providers, or any module containing expected/gold/split values. Inject sentinel labels and prove they do not appear in records, traces, parity results, manifests, or pre-freeze snapshots. Stage A must use only `build_support_lab_scenarios` plus execution-only OpsLab templates.

- [ ] **Step 3: Implement Stage A generation**

`generate_phase5_corpus` executes each matched framework pair before moving to the next repetition, validates parity, records typed mismatches/incompatibilities, and freezes once. Framework execution/infrastructure failures remain corpus entries; they are not silently retried or removed. Formal mode refuses a dirty worktree or unfrozen config.

The command requires an empty destination and supports `--mode pilot|formal`, `--config`, and `--output-dir`. It does not accept labels or provider flags.

- [ ] **Step 4: Implement labels as a separate process and path**

`GoldLabelManifest` stores cell identity, expected failure type, causal-chain/evidence expectations, control flag, split, and content hashes. `gold_specs.py` is the only Phase 5 mapping from SupportLab/OpsLab scenario IDs to those expected values. `generate_phase5_labels` reads only a verified corpus manifest plus `gold_specs`; it never writes into the corpus root. Output defaults to a sibling `*-labels-sealed` directory and refuses overwrite.

The Stage B provider runner must not import or open this directory. Only Task 15's post-call evaluator may load it.

- [ ] **Step 5: Wire explicit CLI commands**

Add:

```text
spanvouch labs corpus --config $CONFIG_PATH --output-dir $EMPTY_CORPUS_DIR
spanvouch labs labels --corpus-dir $FROZEN_CORPUS_DIR --output-dir $EMPTY_LABEL_DIR
```

Both commands default to offline behavior. `labs corpus` returns nonzero if any unapproved parity mismatch occurs. `labs labels` prints only paths/hashes, never label contents.

- [ ] **Step 6: Run offline pilot generation twice**

Run the focused tests, then generate two temporary pilot corpora with the same config and compare logical payload hashes after excluding creation timestamp and Git commit fields.

Run: `uv run pytest tests/evaluation/corpus tests/evaluation/test_run_phase5_corpus.py tests/cli/test_main.py tests/architecture/test_dependency_direction.py -v`

Expected: PASS and zero provider calls.

- [ ] **Step 7: Commit**

```bash
git add src/spanvouch/evaluation/corpus src/spanvouch/evaluation/run_phase5_corpus.py src/spanvouch/evaluation/generate_phase5_labels.py src/spanvouch/cli tests/evaluation tests/cli tests/architecture/test_dependency_direction.py
git commit -m "feat: generate paired Phase 5 trace corpus"
```

### Task 10: Prepare and Freeze DeepSeek Diagnosis Candidates

**Files:**
- Create: `src/spanvouch/diagnosis/prompting.py`
- Create: `tests/diagnosis/test_prompting.py`
- Modify: `src/spanvouch/diagnosis/llm_diagnoser.py`
- Modify: `tests/diagnosis/test_llm_diagnoser.py`
- Create: `src/spanvouch/evaluation/experiments/diagnosis.py`
- Create: `tests/evaluation/experiments/test_diagnosis.py`

**Interfaces:**
- Consumes: verified frozen trace, diagnostic projection/evidence catalog, `ModelProvider`, fixed DeepSeek config.
- Produces: `DiagnosisPromptBuilder`, `PreparedDiagnosis`, `FrozenDiagnosisCandidate`, `DiagnosisCandidateRepository`, `generate_and_freeze_diagnosis`, and reconstructable B2/B3 message inputs.

- [ ] **Step 1: Characterize existing DeepSeek diagnosis messages and reports**

Add tests that snapshot the exact system/user message hashes for clean, diagnosed, and unsupported traces. Assert refactoring does not change `prompt_version="diagnosis-v1"`, provider payload, parsing, invalid-output abstention, evidence resolution, or `DiagnosisReport` hashes.

- [ ] **Step 2: Extract a pure prompt builder**

```python
class PreparedDiagnosis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    messages: tuple[ChatMessage, ...]
    generation: GenerationConfig
    prompt_version: str
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)


class DiagnosisPromptBuilder:
    def prepare(
        self,
        context: DiagnosticContext,
        evidence: EvidenceCatalog,
        generation: GenerationConfig,
    ) -> PreparedDiagnosis: ...

    def shared_verifier_messages(
        self,
        prepared: PreparedDiagnosis,
        frozen_report: DiagnosisReport,
        verifier_instruction: str,
    ) -> tuple[ChatMessage, ...]: ...
```

`shared_verifier_messages` returns the original diagnosis system/user messages, an assistant message containing canonical JSON of the frozen diagnosis decision, and one new user critique instruction. It never persists the provider's raw response envelope or hidden reasoning. `LlmDiagnoser` delegates preparation to this builder and retains accepted behavior.

- [ ] **Step 3: Write failing frozen-candidate tests**

Assert one candidate binds corpus cell, trace hash, diagnostic-context hash, evidence-catalog hash, report hash, generation config hash, prompt hash, generator model/provider, usage, and request ID hash. It must not contain raw prompt strings, raw provider content, credentials, labels, split, or expected findings.

Assert attempts to freeze a second different candidate for the same cell fail with no-replace semantics.

- [ ] **Step 4: Implement diagnosis generation and repository**

`generate_and_freeze_diagnosis` verifies corpus hashes, builds only the sanitized `DiagnosticContext`, calls DeepSeek once, validates the `DiagnosisExecution`, serializes the canonical `DiagnosisReport`, and publishes a content-addressed candidate. Contract-invalid/provider failures produce typed experiment failures, not fabricated diagnoses.

The repository stores reconstructable hashes/configuration and the structured report, not raw messages or response bodies. B2 messages are rebuilt from the checked-in prompt builder, frozen diagnostic context, and frozen report; the rebuilt hash must match a pre-call audit hash.

- [ ] **Step 5: Run diagnosis regression and isolation tests**

Run: `uv run pytest tests/diagnosis tests/evaluation/experiments/test_diagnosis.py -v`

Expected: PASS with all prior diagnosis tests unchanged.

Run: `uv run ruff check src/spanvouch/diagnosis src/spanvouch/evaluation/experiments tests/diagnosis tests/evaluation/experiments && uv run mypy`

Expected: both commands exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/spanvouch/diagnosis src/spanvouch/evaluation/experiments/diagnosis.py tests/diagnosis tests/evaluation/experiments/test_diagnosis.py
git commit -m "feat: freeze reproducible diagnosis candidates"
```

### Task 11: Add a Pinned OpenAI-Compatible Qwen/vLLM Adapter

**Files:**
- Create: `src/spanvouch/adapters/models/openai_compatible.py`
- Create: `tests/adapters/models/test_openai_compatible.py`
- Modify: `src/spanvouch/diagnosis/protocols.py`
- Modify: `.env.example`
- Create: `docs/research/phase5-vllm-runbook.md`
- Create: `evals/configs/phase5-qwen-vllm.example.json`

**Interfaces:**
- Consumes: existing `ChatMessage`, extended `GenerationConfig`, httpx and vLLM Chat Completions.
- Produces: `OpenAICompatibleConfig`, `OpenAICompatibleProvider`, `validate_served_model`, and optional generation `extra_body` needed for Qwen non-thinking structured JSON.

- [ ] **Step 1: Write HTTP contract tests with `httpx.MockTransport`**

Cover exact URL, auth, model, messages, `response_format`, max tokens, temperature, `chat_template_kwargs.enable_thinking=false`, timeout, one bounded retry, 429/5xx/transport failure, invalid JSON, empty choices, null content, wrong served model, and usage parsing. Capture the outbound body and assert no evaluator sentinel appears.

- [ ] **Step 2: Extend generation configuration without changing DeepSeek payloads**

Add:

```python
class GenerationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    model: str = "deepseek-v4-flash"
    max_tokens: int = Field(default=1200, ge=1, le=4096)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    extra_body: dict[str, JsonValue] = Field(default_factory=dict)
```

DeepSeek tests must prove an empty `extra_body` produces byte-equivalent request fields to Phase 4. The generic adapter merges `extra_body` only after rejecting collisions with `model`, `messages`, `stream`, `response_format`, `max_tokens`, and `temperature`.

- [ ] **Step 3: Implement the generic provider**

`OpenAICompatibleConfig` contains `base_url`, secret API key, connect/read timeouts, at most one retry, expected model, and endpoint class. `OpenAICompatibleProvider.complete` uses `/v1/chat/completions`, JSON-object response format, explicit nonstreaming, typed errors, and existing `ProviderResponse`/`ProviderUsage`.

`validate_served_model` calls `/v1/models`, requires the configured model ID, and records only endpoint class/model/version headers allowed by the audit policy.

- [ ] **Step 4: Add environment names and the GPU runbook**

Add empty `SPANVOUCH_VLLM_BASE_URL` and `SPANVOUCH_VLLM_API_KEY` variables. The runbook must require Linux and resolve the selected `vllm/vllm-openai` tag to its immutable `RepoDigests` value with `docker image inspect` immediately before the pilot. It must record that full digest, `Qwen/Qwen3-14B` plus exact Hugging Face revision, non-thinking chat template behavior, GPU model/driver/CUDA capture, `/v1/models` smoke test, JSON schema smoke test, firewall/TLS/API-key controls, shutdown, and cost recording.

The example config is a complete localhost smoke configuration using `http://127.0.0.1:8000/v1`, model `Qwen/Qwen3-14B`, non-thinking mode and no credentials. It is labeled `smoke_only=true`; pilot/formal validation rejects `smoke_only` and requires concrete image/checkpoint digests.

- [ ] **Step 5: Run provider and regression tests**

Run: `uv run pytest tests/adapters/models tests/diagnosis/test_llm_diagnoser.py tests/verification/test_semantic.py -v`

Expected: PASS with no network call.

Run: `uv run ruff check src/spanvouch/adapters/models src/spanvouch/diagnosis tests/adapters/models && uv run mypy`

Expected: both commands exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/spanvouch/adapters/models/openai_compatible.py src/spanvouch/diagnosis/protocols.py tests/adapters/models/test_openai_compatible.py .env.example docs/research/phase5-vllm-runbook.md evals/configs/phase5-qwen-vllm.example.json
git commit -m "feat: add pinned Qwen vLLM provider"
```

### Task 12: Enforce Request Caching, Cost Budgets and Paid-Run Gates

**Files:**
- Create: `src/spanvouch/evaluation/experiments/provider.py`
- Create: `src/spanvouch/evaluation/experiments/budget.py`
- Create: `tests/evaluation/experiments/test_provider.py`
- Create: `tests/evaluation/experiments/test_budget.py`
- Create: `evals/configs/phase5-pricing.example.json`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `ModelProvider`, experiment config, canonical request identity, provider usage, user-supplied pricing and GPU lease records.
- Produces: `RequestIdentity`, `ProviderRequestAudit`, `ProviderResultCache`, `BudgetLedger`, `GuardedProvider`, and `PaidRunAuthorization`.

- [ ] **Step 1: Write cache-identity and no-rebilling tests**

```python
def test_request_identity_changes_for_every_causal_input(base_request: RequestIdentity) -> None:
    fields = (
        "trace_sha256",
        "diagnosis_sha256",
        "condition_id",
        "prompt_version",
        "provider",
        "model",
        "generation_config_sha256",
    )
    for field in fields:
        changed = base_request.model_copy(update={field: "changed"})
        assert changed.sha256 != base_request.sha256


@pytest.mark.asyncio
async def test_cache_hit_preserves_original_usage_and_makes_no_call(
    guarded_provider: GuardedProvider,
    fake_provider: CountingProvider,
) -> None:
    first = await guarded_provider.complete(MESSAGES, GENERATION)
    second = await guarded_provider.complete(MESSAGES, GENERATION)
    assert fake_provider.calls == 1
    assert second.response == first.response
    assert second.cache_hit is True
    assert second.original_usage == first.original_usage
```

- [ ] **Step 2: Write budget reservation tests**

Cover CNY decimal arithmetic, pilot 10% cap, 80% global stop, concurrent reservation, reservation release on failure, committed actual cost, unknown price, GPU lease cost, month rollover, incomplete paired-matrix pause, and rejection without both live authorization flags.

- [ ] **Step 3: Implement a local SQLite operational cache/ledger**

Use one evaluation-owned SQLite file under `.cache/phase5/`; it is not a production database or artifact. Tables are append-only request identity/result, reservation, charge, and GPU lease records. Use `BEGIN IMMEDIATE` to atomically check and reserve budget before any live call. Never store an API key, Authorization header, raw request body, raw provider envelope, hidden reasoning, gold label, split, or expected finding.

The cached value is the validated `ProviderResponse` fields needed to reconstruct a response plus usage and sanitized provenance. Secret-classify content before local persistence and publish only parsed diagnosis/verifier contracts to artifact bundles.

- [ ] **Step 4: Implement paid authorization and guarded calls**

```python
class PaidRunAuthorization(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    experiment_id: str = Field(min_length=1)
    allow_live_provider: bool = False
    formal_run: bool = False
    frozen_manifest_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)

    def require(self, mode: ExperimentMode) -> None:
        if not self.allow_live_provider:
            raise ProviderConfigurationError("live provider access is disabled")
        if mode is ExperimentMode.FORMAL and (
            not self.formal_run or self.frozen_manifest_sha256 is None
        ):
            raise ProviderConfigurationError("formal live run requires frozen manifest")
```

`GuardedProvider` computes request identity before calling, returns verified cache hits, reserves maximum estimated cost, calls once through the bounded provider, commits actual usage/cost, and releases unused reservation in `finally`. It captures a safe `ProviderRequestAudit` containing hashes, field names, model/provider, timestamps, status, and leakage scan result, never message content.

- [ ] **Step 5: Add pricing and ignore rules**

The pricing example records currency, effective date, source URL, per-million input/output token prices, GPU hourly price, and whether amounts are billed or estimated. It contains no hardcoded claim that the example is current. `.gitignore` excludes `.cache/phase5/` and all local provider/GPU credential material.

- [ ] **Step 6: Run cache, budget, secret and concurrency tests**

Run: `uv run pytest tests/evaluation/experiments/test_provider.py tests/evaluation/experiments/test_budget.py tests/review/test_secret_hygiene.py -v`

Expected: PASS and the fake provider count proves no call occurs without authorization or on cache hit.

Run: `uv run ruff check src/spanvouch/evaluation/experiments tests/evaluation/experiments && uv run mypy`

Expected: both commands exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/spanvouch/evaluation/experiments/provider.py src/spanvouch/evaluation/experiments/budget.py tests/evaluation/experiments evals/configs/phase5-pricing.example.json .gitignore
git commit -m "feat: guard Phase 5 paid provider runs"
```

### Task 13: Define the Complete B0-B5 Matrix and Failure Records

**Files:**
- Create: `src/spanvouch/evaluation/experiments/models.py`
- Create: `src/spanvouch/evaluation/experiments/planner.py`
- Create: `tests/evaluation/experiments/test_models.py`
- Create: `tests/evaluation/experiments/test_planner.py`

**Interfaces:**
- Consumes: frozen eligible corpus cells, one frozen diagnosis per cell, six ordered conditions and experiment config.
- Produces: `ExperimentFailureCategory`, `ConditionStatus`, `SelectiveAction`, `ConditionPlan`, `ConditionResult`, `ExperimentMatrixManifest`, and `VerificationMatrixPlanner.plan`.

- [ ] **Step 1: Write failing research-record tests**

Require immutable, extra-forbid records and these disjoint failure values:

```python
class ExperimentFailureCategory(StrEnum):
    FRAMEWORK_EXECUTION = "framework_execution_failure"
    FRAMEWORK_INCOMPATIBILITY = "framework_incompatibility"
    INFRASTRUCTURE = "infrastructure_failure"
    PROVIDER = "provider_failure"
    CONTRACT_INVALID = "contract_invalid"
    DIAGNOSIS = "diagnosis_error"
    VERIFICATION = "verification_error"
```

Operational categories must not carry `is_correct`; diagnosis/verification categories require post-call evaluator provenance and cannot be constructed by the provider runner.

`ConditionResult` binds plan ID, cell, trace/diagnosis hashes, condition, status, selective action, verifier report hashes, request-audit hashes, usage/cost, cache status, timing, and typed operational failure. It cannot contain gold labels, expected findings, split identity, or another condition's full result.

- [ ] **Step 2: Write the complete-matrix planner test**

```python
def test_planner_emits_six_conditions_per_eligible_candidate(
    eligible_candidates: tuple[FrozenDiagnosisCandidate, ...],
    config: Phase5ExperimentConfig,
) -> None:
    plans = VerificationMatrixPlanner().plan(eligible_candidates, config)
    assert len(plans) == len(eligible_candidates) * 6
    for candidate in eligible_candidates:
        conditions = {
            plan.condition_id
            for plan in plans
            if plan.diagnosis_sha256 == candidate.report_sha256
        }
        assert conditions == set(ConditionId)
```

Add failures for missing/duplicate conditions, diagnosis mismatch, trace mismatch, config mismatch, unpaired framework/repetition cells, provider config drift, and selectively omitted expensive cells.

- [ ] **Step 3: Implement deterministic plan identities**

`ConditionPlan.plan_id` is the canonical hash of experiment ID, corpus cell, trace hash, diagnosis hash, condition ID, prompt version, provider/model and generation config. Plans sort by domain/template/scenario/framework/repetition/condition enum order. B0/B1 have provider status `not_required`; B2/B3 use DeepSeek; B4/B5 use Qwen.

`ExperimentMatrixManifest` records all plan IDs, eligible/ineligible cells, typed reasons, condition counts, config/corpus/candidate-manifest hashes and no label path/hash.

- [ ] **Step 4: Run planner and leakage tests**

Run: `uv run pytest tests/evaluation/experiments/test_models.py tests/evaluation/experiments/test_planner.py tests/evaluation/corpus/test_label_isolation.py -v`

Expected: PASS and exactly six plans per eligible candidate.

- [ ] **Step 5: Commit**

```bash
git add src/spanvouch/evaluation/experiments/models.py src/spanvouch/evaluation/experiments/planner.py tests/evaluation/experiments/test_models.py tests/evaluation/experiments/test_planner.py
git commit -m "feat: plan complete Phase 5 verification matrix"
```

### Task 14: Implement B0-B5 Verification Conditions

**Files:**
- Create: `src/spanvouch/verification/prompting.py`
- Create: `tests/verification/test_prompting.py`
- Modify: `src/spanvouch/verification/semantic.py`
- Modify: `tests/verification/test_semantic.py`
- Create: `src/spanvouch/evaluation/experiments/conditions.py`
- Create: `tests/evaluation/experiments/test_conditions.py`

**Interfaces:**
- Consumes: one `ConditionPlan`, frozen diagnosis/context/evidence, deterministic verifier, guarded DeepSeek/Qwen providers.
- Produces: `SemanticPromptBuilder`, `ConditionExecutor`, `ConditionExecutionContext`, and exact B0-B5 `ConditionResult` behavior.

- [ ] **Step 1: Characterize and extract semantic prompt preparation**

Snapshot current isolated semantic message hashes before refactoring. Extract:

```python
class PreparedVerification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    messages: tuple[ChatMessage, ...]
    generation: GenerationConfig
    prompt_version: str
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)


class SemanticPromptBuilder:
    def isolated(
        self,
        input_: VerificationInput,
        catalog: EvidenceCatalog,
        generation: GenerationConfig,
    ) -> PreparedVerification: ...

    def shared(
        self,
        diagnosis_messages: tuple[ChatMessage, ...],
        input_: VerificationInput,
        catalog: EvidenceCatalog,
        generation: GenerationConfig,
    ) -> PreparedVerification: ...
```

Both methods use one byte-identical verifier instruction and one output schema. `shared` prepends the diagnosis generation system/user plus assistant canonical frozen diagnosis; `isolated` contains only the verifier instruction and contract-allowed evidence view. `SemanticVerifier` delegates to this builder and preserves all Phase 4 isolated behavior.

- [ ] **Step 2: Write one failing test per condition**

Assert:

- B0 accepts every contract-valid diagnosis with no verifier/provider call;
- B1 accepts only a deterministic `VERIFIED` report;
- B2 calls DeepSeek with the audited shared sequence;
- B3 calls DeepSeek in a new isolated sequence;
- B4 calls Qwen with exactly the B3 evidence view and no DeepSeek context;
- B5 runs deterministic first and calls Qwen only when deterministic verification passes; otherwise it records `not_invoked_by_policy` and requires review;
- every nonverified/invalid/provider failure yields `REVIEW_REQUIRED`, never silent acceptance;
- all conditions consume the same `report_sha256` bytes.

- [ ] **Step 3: Implement the condition context and dispatcher**

```python
class ConditionExecutionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)
    plan: ConditionPlan
    verification_input: VerificationInput
    diagnosis_messages: tuple[ChatMessage, ...]


class ConditionExecutor:
    async def execute(
        self,
        context: ConditionExecutionContext,
        *,
        deterministic: Verifier,
        deepseek: GuardedProvider,
        qwen: GuardedProvider,
    ) -> ConditionResult: ...
```

The executor creates a fresh `SemanticVerifier` per semantic condition, verifies the exact plan/request hashes, and records parsed reports plus audit hashes. It does not accept labels or a label repository in its constructor or method signature.

- [ ] **Step 4: Enforce B2/B3 causal equality tests**

Capture both outbound requests and assert equality of model, verifier-instruction bytes, output schema, temperature, max tokens, diagnosis bytes, trace/evidence view and allowed selectors. Assert the only allowed message difference is the presence of diagnosis-generation history in B2. Any other difference fails the test.

- [ ] **Step 5: Run verification and condition tests**

Run: `uv run pytest tests/verification tests/evaluation/experiments/test_conditions.py -v`

Expected: PASS, including all invalid-output and provider-failure paths.

Run: `uv run ruff check src/spanvouch/verification src/spanvouch/evaluation/experiments tests/verification tests/evaluation/experiments && uv run mypy`

Expected: both commands exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/spanvouch/verification src/spanvouch/evaluation/experiments/conditions.py tests/verification tests/evaluation/experiments/test_conditions.py
git commit -m "feat: implement Phase 5 verification conditions"
```

### Task 15: Run the Matrix and Join Gold Only After Provider Completion

**Files:**
- Create: `src/spanvouch/evaluation/experiments/runner.py`
- Create: `src/spanvouch/evaluation/run_phase5_matrix.py`
- Create: `src/spanvouch/evaluation/evaluate_phase5_matrix.py`
- Create: `tests/evaluation/experiments/test_runner.py`
- Create: `tests/evaluation/experiments/test_post_call_join.py`
- Create: `tests/evaluation/test_run_phase5_matrix.py`
- Modify: `src/spanvouch/cli/main.py`
- Modify: `tests/cli/test_main.py`

**Interfaces:**
- Consumes: verified corpus/candidates/matrix, condition executor, guarded providers, and sealed labels only in a separate evaluator entry point.
- Produces: `ExperimentRunner.run_provider_phase`, `ProviderPhaseManifest`, `EvaluatedConditionResult`, `PostCallEvaluator.join`, and CLI commands `spanvouch experiments run` / `spanvouch experiments evaluate`.

- [ ] **Step 1: Write a real provider-phase leakage test**

Install sentinels in sealed label values and filesystem path names. Capture every object and serialized byte reachable by the runner immediately before provider calls. Assert label modules are not imported, label paths are not opened, and no sentinel appears in messages, audits, plans, results, exceptions, logs, or provider-phase artifacts.

- [ ] **Step 2: Write matrix completeness and failure-semantics tests**

Simulate success, cache hit, framework execution failure, incompatibility, infrastructure failure, provider failure, contract-invalid diagnosis/verifier output, cancellation and budget pause. Require:

- every plan ends as completed, failed with a typed category, not invoked by policy, or paused before any new paid call;
- no plan disappears;
- first four failure categories never count as correct abstention;
- provider/contract failures cannot become accepted;
- interrupted runs resume only from verified cache/results and preserve plan identity;
- a partially affordable paired matrix pauses rather than dropping costly conditions.

- [ ] **Step 3: Implement provider phase with atomic result publication**

The runner verifies all parent manifests, iterates canonical plan order, checks budget before scheduling, and writes each condition result under its plan hash using no-replace publication. It may parallelize only within the frozen concurrency limit and must never concurrently use one AutoGen agent or mutable provider session.

Publish `ProviderPhaseManifest` containing result hashes, status counts, usage/cost, missingness and config/corpus/candidate/matrix hashes. Set `provider_phase_complete=true` only when every scheduled plan has a non-paused terminal result; a budget/provider pause produces `provider_phase_complete=false`, and post-call evaluation is forbidden. It contains no gold/expected/split data.

- [ ] **Step 4: Implement the separate post-call evaluator**

`PostCallEvaluator.join(provider_manifest, sealed_labels)` refuses to run unless `provider_phase_complete=true`, all result hashes verify, and the label/corpus cell sets match. It attaches correctness, diagnosis error, verification error, family, control and split only to `EvaluatedConditionResult` in a new evaluation output directory.

The evaluator has no provider, model or live-call dependency and must work with network disabled.

- [ ] **Step 5: Wire explicit CLI boundaries**

```text
spanvouch experiments run --config $FROZEN_CONFIG --corpus-dir $CORPUS_DIR --candidate-dir $CANDIDATE_DIR --output-dir $EMPTY_PROVIDER_RESULT_DIR [--allow-live-provider --approved-manifest-sha256 $APPROVED_MATRIX_SHA256] [--formal-run]
spanvouch experiments evaluate --provider-results $PROVIDER_RESULT_DIR --sealed-labels $SEALED_LABEL_DIR --output-dir $EMPTY_EVALUATED_RESULT_DIR
```

The run command has no label argument. The evaluate command has no endpoint, API key, live-provider or formal-run argument.

- [ ] **Step 6: Run runner, process-isolation and CLI tests**

Run: `uv run pytest tests/evaluation/experiments/test_runner.py tests/evaluation/experiments/test_post_call_join.py tests/evaluation/test_run_phase5_matrix.py tests/cli/test_main.py -v`

Expected: PASS with zero real provider calls.

- [ ] **Step 7: Commit**

```bash
git add src/spanvouch/evaluation/experiments/runner.py src/spanvouch/evaluation/run_phase5_matrix.py src/spanvouch/evaluation/evaluate_phase5_matrix.py src/spanvouch/cli tests/evaluation tests/cli
git commit -m "feat: run isolated verification matrix"
```

### Task 16: Implement Risk-Coverage Metrics and Statistical Inference

**Files:**
- Create: `src/spanvouch/evaluation/statistics/__init__.py`
- Create: `src/spanvouch/evaluation/statistics/metrics.py`
- Create: `src/spanvouch/evaluation/statistics/inference.py`
- Create: `tests/evaluation/statistics/test_metrics.py`
- Create: `tests/evaluation/statistics/test_inference.py`
- Create: `tests/evaluation/statistics/fixtures/known-effects.json`

**Interfaces:**
- Consumes: fully joined `EvaluatedConditionResult` values and preregistered seed/comparisons.
- Produces: `ConditionMetrics`, `RiskCoveragePoint`, `PairedEffect`, `ClusterBootstrapResult`, `McNemarResult`, `HolmResult`, `compute_condition_metrics`, `paired_cluster_bootstrap`, `exact_mcnemar`, and `holm_adjust`.

- [ ] **Step 1: Write denominator and edge-case tests**

Use tiny hand-calculated fixtures to assert:

```text
false_acceptance_risk = accepted incorrect diagnoses / all accepted diagnoses
coverage              = accepted diagnoses / all eligible diagnosis candidates
```

Framework execution, framework incompatibility and pre-diagnosis infrastructure failures have no diagnosis candidate and are excluded from the scientific eligible-candidate denominator under the frozen missingness rule. Provider and verifier contract failures after a candidate exists are reported separately and produce review-required; the primary table shows both the preregistered scientific coverage and an all-scheduled sensitivity coverage that counts those failures as nonacceptance. Claim gates fail if an apparent risk improvement is explained by differential operational failure. Contract-invalid diagnoses remain scheduled and are not accepted. Zero accepted diagnoses yields `risk=None`, never zero. Always report numerator and denominator beside a ratio.

Test family accuracy, causal-chain correctness, grounding, disagreement, joint error, invalid output, abstention, framework/provider/infra failure, tokens, cost and latency.

- [ ] **Step 2: Write risk-coverage curve tests**

Thresholds are the sorted unique verifier confidence values plus boundary values 0 and 1. B0/B1 binary policies receive their fixed operating point rather than fabricated continuous curves. Assert monotone coverage as the threshold rises and stable tie handling.

- [ ] **Step 3: Write known-effect inference tests**

The checked-in fixture contains:

- an exact zero paired effect;
- a known beneficial B3 effect;
- repetitions that would create false precision if treated as independent;
- discordant pairs for hand-computed exact McNemar p-values;
- four raw p-values with hand-computed Holm-adjusted values.

Assert bootstrap resamples scenario-template clusters and carries all framework/repetition rows for a selected template together. Fix the bootstrap seed and default to 10,000 draws for formal analysis.

- [ ] **Step 4: Implement pure deterministic statistics**

Use stdlib `random.Random`, `math.comb`, `statistics`, and Decimal-safe aggregation; do not add pandas/scipy as runtime dependencies. Percentile intervals use a documented nearest-rank rule. Exact McNemar uses a two-sided binomial tail on discordant pairs. Holm sorts by `(p_value, comparison_id)` and enforces monotone adjusted p-values.

For H1, bootstrap paired completion-rate differences `AutoGen - LangGraph`. For H2/H3, bootstrap B3/B4 minus B2 false-acceptance risk and coverage on matched cells. If any resample has no accepted diagnoses for either condition, record the undefined draw rate and fail the claim gate when it exceeds the preregistered tolerance.

- [ ] **Step 5: Run statistical tests and static checks**

Run: `uv run pytest tests/evaluation/statistics -v`

Expected: PASS for hand-calculated effects, clustering, edge cases and deterministic repeatability.

Run: `uv run ruff check src/spanvouch/evaluation/statistics tests/evaluation/statistics && uv run mypy`

Expected: both commands exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/spanvouch/evaluation/statistics tests/evaluation/statistics
git commit -m "feat: add Phase 5 statistical analysis"
```

### Task 17: Generate Claim Gates and Paper-Ready Assets

**Files:**
- Create: `src/spanvouch/evaluation/statistics/claims.py`
- Create: `src/spanvouch/evaluation/paper_assets.py`
- Create: `src/spanvouch/evaluation/run_phase5_analysis.py`
- Create: `tests/evaluation/statistics/test_claims.py`
- Create: `tests/evaluation/test_paper_assets.py`
- Create: `tests/evaluation/test_run_phase5_analysis.py`
- Modify: `src/spanvouch/evaluation/artifacts.py`
- Modify: `tests/evaluation/test_artifacts.py`
- Create: `docs/paper/method.md`
- Create: `docs/paper/experiment-setup.md`
- Create: `docs/paper/results.md`
- Modify: `docs/research/ivad-claim-evidence-ledger.md`
- Modify: `src/spanvouch/cli/main.py`

**Interfaces:**
- Consumes: evaluated results, statistical records, artifact manifests and preregistered thresholds.
- Produces: `HypothesisOutcome`, `ClaimGateReport`, `evaluate_claim_gates`, deterministic CSV/Markdown/SVG assets, paper-section drafts and `spanvouch experiments analyze`.

- [ ] **Step 1: Write one claim-gate test per hypothesis**

Require explicit `supported`, `contradicted`, or `unresolved` outcomes:

- H1: both adapters at least 95% contract-valid and lower 95% CI bound for AutoGen-LangGraph completion above -0.05;
- H2: B3-B2 risk upper 95% CI below zero, coverage loss within frozen tolerance, and beneficial effect direction in both frameworks;
- H3: B4-B2 risk upper 95% CI below zero, lower conditional joint error, coverage within tolerance, and beneficial effect direction in both frameworks;
- H4: every improvement has coverage/risk-coverage evidence and is not caused by provider/invalid-output failures;
- H5: OpsLab direction reported with uncertainty and never promoted to broad generalization.

Missing cells, undefined risk, excessive coverage loss, incomplete manifests, inconsistent framework direction or Holm failure must prevent `supported`.

- [ ] **Step 2: Write deterministic asset tests**

Given a fixed analysis fixture, generate byte-identical:

```text
metrics-by-condition.csv
paired-effects.csv
failure-accounting.csv
risk-coverage.csv
claim-gates.json
main-results.md
risk-coverage.svg
manifest.json
```

Assert every displayed number has a source artifact hash, numerator/denominator where relevant, confidence interval method/seed, and no credential/raw prompt/response/hidden reasoning.

- [ ] **Step 3: Extend the artifact writer with a typed Phase 5 bundle config**

Add a `Phase5BundleConfig` model containing only experiment ID/mode, config/corpus/candidate/matrix/provider/evaluated-result hashes, analysis seed, bootstrap draws and policy versions. `ArtifactBundleWriter` accepts this model by canonical JSON conversion instead of weakening the existing arbitrary-config whitelist. Tests reject extra/nested secret fields and prove Phase 4 bundle bytes remain unchanged.

- [ ] **Step 4: Implement claim gates and asset writers**

Use canonical ordering and fixed decimal formatting. SVG generation uses a small deterministic writer with explicit axes, labels and accessible title/description; it consumes `risk-coverage.csv` rather than recomputing metrics. Markdown tables include SupportLab primary, OpsLab preliminary, framework-stratified results, costs, missingness and null/negative outcomes.

- [ ] **Step 5: Add paper skeletons with claim discipline**

`method.md` documents IVAD, isolated evidence boundary, B0-B5 and two-stage replay. `experiment-setup.md` documents labs, models, prompts/config hashes, repetitions, endpoints, statistics, budget and exclusions. `results.md` contains generated include markers plus prose rules: portability is not equivalence, OpsLab is not broad generalization, disagreement is not correctness, B4 is operational cross-model evidence, and null findings remain visible.

The claim ledger changes from `planned` only when the analysis manifest exists; each row links exact artifact IDs and says supported/contradicted/unresolved.

- [ ] **Step 6: Wire offline analysis CLI**

```text
spanvouch experiments analyze --evaluated-results $EVALUATED_RESULT_DIR --config $FROZEN_CONFIG --output-dir $EMPTY_ANALYSIS_DIR
```

The command has no provider, endpoint, API-key, live-run or label arguments. It regenerates all statistics/assets from joined results and fails if a number lacks manifest provenance.

- [ ] **Step 7: Run analysis and paper-asset tests**

Run: `uv run pytest tests/evaluation/statistics tests/evaluation/test_paper_assets.py tests/evaluation/test_run_phase5_analysis.py -v`

Expected: PASS and byte-identical repeated output.

- [ ] **Step 8: Commit**

```bash
git add src/spanvouch/evaluation/statistics/claims.py src/spanvouch/evaluation/paper_assets.py src/spanvouch/evaluation/run_phase5_analysis.py src/spanvouch/evaluation/artifacts.py src/spanvouch/cli/main.py tests/evaluation docs/paper docs/research/ivad-claim-evidence-ledger.md
git commit -m "feat: generate Phase 5 research evidence"
```

### Task 18: Complete Reproducibility, CI, Paid Checkpoints and Phase 5 Acceptance

**Files:**
- Create: `tests/evaluation/test_phase5_offline_e2e.py`
- Create: `tests/architecture/test_phase5_boundaries.py`
- Create: `docs/evaluation/phase5-reproduction-runbook.md`
- Create: `docs/evaluation/phase5-acceptance.md`
- Create: `evals/reports/reference/phase5-offline-smoke/README.md`
- Create: `evals/reports/reference/phase5-offline-smoke/manifest.json`
- Create: `evals/reports/reference/phase5-offline-smoke/metrics.json`
- Create: `evals/reports/reference/phase5-offline-smoke/config.json`
- Create: `evals/reports/reference/phase5-offline-smoke/environment.txt`
- Create: `evals/reports/reference/phase5-offline-smoke/structured-events.jsonl`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `docs/research/reproducibility.md`
- Modify: `docs/research/ivad-claim-evidence-ledger.md`

**Interfaces:**
- Consumes: all Phase 5 components, explicit paid-run approvals and final manifests.
- Produces: zero-provider end-to-end regression evidence, optional approved pilot/formal evidence, acceptance report and a reproducible handoff for the paper-writing phase.

- [ ] **Step 1: Add the zero-provider end-to-end test**

The test builds a small SupportLab+OpsLab fixture, executes both adapters, freezes/replays the corpus, creates deterministic fake diagnoses/provider results, runs B0-B5, performs the post-call label join, computes statistics and regenerates assets. Patch sockets/httpx live transport to raise if any network call occurs.

Run: `uv run pytest tests/evaluation/test_phase5_offline_e2e.py -v`

Expected: PASS and an identical logical manifest on two runs.

- [ ] **Step 2: Add Phase 5 architecture and security gates**

Assert:

- production core cannot import labs/evaluation;
- Stage A cannot import labels/providers/statistics;
- provider runner cannot import/open sealed labels;
- post-call evaluator cannot import providers;
- within `spanvouch.labs`, only `labs/frameworks/langgraph.py` imports LangGraph for Stage A and only `labs/frameworks/autogen.py` imports AutoGen; the existing production review adapter remains outside this lab-only assertion;
- Stage B runner imports neither framework;
- provider-visible models exclude forbidden fields;
- artifact/reference trees contain no credentials, raw bodies, prompts or hidden reasoning.

- [ ] **Step 3: Extend CI with offline Phase 5 gates**

Add focused architecture/e2e commands after the existing test job. CI must never set provider keys, call live endpoints, start vLLM, rent a GPU, or execute paid commands. Preserve wheel and Docker jobs.

- [ ] **Step 4: Build and verify the offline reference bundle**

From a clean committed candidate, generate the small fake-provider reference bundle twice. Compare metrics/config/events/README byte-for-byte and verify manifest payload hashes. Commit the first accepted bundle and document exact reproduction commands.

- [ ] **Step 5: Run the complete local quality gate**

Run:

```powershell
uv sync --all-groups
uv run ruff check src tests
uv run mypy
uv run pytest --cov=spanvouch --cov-report=term-missing
uv run pytest tests/contracts tests/architecture tests/test_delivery_config.py -v
uv build --wheel --build-constraints build-constraints.txt --require-hashes --no-cache
docker compose config --quiet
docker compose build api
docker compose up --detach --wait --wait-timeout 90 api
docker compose exec -T api sh -c 'test "$(id -u):$(id -g)" = "10001:10001"'
docker compose restart api
docker compose down --volumes --remove-orphans
```

Expected: Ruff/mypy/tests/build/Docker all exit 0, total coverage at least 93%, container remains non-root, restart/persistence checks remain accepted, and no Docker residue remains.

- [ ] **Step 6: Stop for explicit paid-pilot approval**

Present the frozen pilot config hash, exact DeepSeek pricing source, exact Qwen checkpoint revision, exact vLLM image digest, GPU quote/type, projected calls/tokens/GPU hours, maximum pilot spend, shutdown command and rollback. Do not rent a GPU or make a paid call until the user approves that concrete spend.

- [ ] **Step 7: Execute and review the approved pilot**

If approved, execute three repetitions per cell, keep the paid amount within 10% of the approved monthly cap, shut down the GPU immediately after Qwen outputs are cached, and produce a pilot-only report. Use it only to validate pipeline/variance, select 5-20 formal repetitions, and freeze coverage tolerance at no more than 0.10. Do not include pilot rows in formal results.

If the pilot exposes a contract, leakage, parity, invalid-output, cost, or serving incompatibility, stop before formal freeze, record it, fix through a new reviewed task/commit, and rerun the pilot under a new experiment identity.

- [ ] **Step 8: Freeze the formal experiment and stop for formal-spend approval**

Write the formal config with selected repetitions, coverage tolerance, exclusion/missingness rules, prompt/model/image/checkpoint hashes, bootstrap seed/draw count, analysis commit and expected maximum spend. Seal labels and analysis scripts. Present this manifest and quote to the user; do not proceed without explicit approval.

- [ ] **Step 9: Execute the approved formal matrix without selective shrinkage**

If approved, run complete paired cells in canonical order with caching/budget controls. On 80% budget stop, provider outage, GPU loss or incomplete paired cells, pause the whole matrix and report status; do not remove difficult/expensive cells. After provider phase completion, run the separate label join and offline analysis.

- [ ] **Step 10: Write the Phase 5 acceptance report**

Record exact commits, config/corpus/candidate/matrix/result/analysis manifest hashes, scenario/cell counts, adapter conformance/parity, B0-B5 completeness, failures/missingness, coverage/risk/CI, H1-H5 supported/contradicted/unresolved outcomes, cost, provider/GPU provenance, security checks, test/coverage/static/build/Docker evidence, null/negative results, limitations and Phase 6 handoff.

Phase 5 is not accepted if paid formal evidence is absent, unless the design thread explicitly redefines completion as engineering-only; the report must never silently substitute fake-provider evidence for a paper result.

- [ ] **Step 11: Final documentation and README update**

README describes the two-stage architecture, both frameworks, SupportLab/OpsLab, offline replay, B0-B5, zero-provider quickstart and exact limitations. Reproducibility docs link the runbook/reference/formal manifests. The claim ledger matches the computed claim-gate JSON exactly.

- [ ] **Step 12: Commit the accepted Phase 5 evidence**

Before committing, rerun secret classification and `git diff --check`. Commit only sanitized, reasonably sized open artifacts; large trace/model cache files stay content-addressed in the documented release location with hashes.

```bash
git add .github README.md docs tests/architecture tests/evaluation evals/reports/reference/phase5-offline-smoke
git commit -m "docs: accept Phase 5 research evidence"
```

---

## Plan Self-Review Checklist

- [ ] Every approved design section maps to at least one Task in the coverage matrix.
- [ ] Task order never requires a type, method or file before the producing Task.
- [ ] The six Contract v1 roots stay unchanged and the core dependency test remains enforced.
- [ ] LangGraph and AutoGen consume the same environment, scenario, limits and terminal predicate.
- [ ] SupportLab retains its accepted 20-scenario behavior and OpsLab has exactly 16 templates/four controls.
- [ ] Stage A imports no labels/providers/statistics and Stage B imports no agent frameworks.
- [ ] B0-B5 consume one byte-identical frozen diagnosis per cell.
- [ ] B2/B3 preserve the stated causal boundary; B4 is not described as a pure model-only intervention.
- [ ] No real provider call, GPU rental or paid action occurs without a concrete approval checkpoint.
- [ ] Budget, cache, failure, missingness, leakage, secret and no-rebilling behavior have explicit tests.
- [ ] Primary risk always has coverage, numerator/denominator and cluster-level uncertainty.
- [ ] Formal pilot separation, repetitions, confidence intervals, McNemar/Holm and claim gates are executable.
- [ ] Every paper number is generated from manifest-bound data; null/negative outcomes remain visible.
- [ ] Full CI remains zero-provider and existing wheel/Docker/persistence/non-root gates remain active.
- [ ] Phase 5 exclusions remain absent from every implementation Task.

## Execution Handoff

Implement from a new Phase 5 worktree/branch created from the accepted Phase 4+planning head. Paid pilot/formal checkpoints remain manual user approvals even during autonomous execution.

1. **Subagent-Driven (recommended):** the coding thread uses `superpowers:subagent-driven-development`, dispatches one fresh implementation subagent per Task, then performs requirement-conformance and code-quality review before accepting each commit.
2. **Inline Execution:** one coding thread uses `superpowers:executing-plans`, executes Tasks in numbered batches and stops at the documented review/paid checkpoints.
