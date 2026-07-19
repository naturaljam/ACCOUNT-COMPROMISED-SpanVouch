# SpanVouch Phase 5: Multi-Framework Execution and Isolated Verification Lab

**Status:** Design approved in the design thread; implementation is not authorized until this written specification is reviewed and an implementation plan is approved.

**Date:** 2026-07-19

## 1. Executive decision

Phase 5 is the first paper-evidence phase after the Phase 4 research foundation. It will not extend SpanVouch by accumulating unrelated product features. It will build a controlled multi-framework laboratory and use it to test a falsifiable claim:

> Holding the task, trace contract, diagnosis candidate, evidence view, and evaluation protocol fixed, does an isolated verifier reduce accepted diagnosis errors compared with same-context self-critique, and does that effect persist across LangGraph and AutoGen?

The approved design is a two-stage hybrid:

1. execute matched scenarios online in LangGraph and AutoGen and freeze their traces;
2. replay the frozen traces offline through a fixed diagnosis and verification matrix.

The approved domain scope is the complete SupportLab plus a 16-template OpsLab pilot. The approved model scope is DeepSeek as the primary diagnosis generator and a Qwen-class open model served through vLLM as the cross-model verifier.

Phase 5 succeeds by producing valid, reproducible evidence, including a null or negative result. It does not require a positive paper claim.

## 2. Phase boundary

### 2.1 In scope

- a framework-neutral `AgentRuntimeAdapter` port;
- a LangGraph runtime adapter and an AutoGen runtime adapter;
- matched scenario execution and parity validation;
- the complete existing SupportLab expressed through the shared runtime port;
- an OpsLab pilot with four failure families and 16 templates;
- immutable framework execution records and trace replay;
- DeepSeek diagnosis generation over frozen traces;
- deterministic, same-model shared-context, same-model isolated, and cross-model isolated verification conditions;
- preregistered paired statistical analysis;
- paper-ready tables, risk-coverage curves, ablations, and artifact bundles;
- updates to the IVAD claim-evidence ledger based on observed evidence.

### 2.2 Out of scope

- Conformal risk control or calibration claims;
- Level 1 or Level 2 evidence acquisition;
- CodeLab;
- Repair Agent or automatic remediation;
- human-subject or expert-rating experiments;
- broad OOD/generalization claims;
- authentication, RBAC, distributed queues, Postgres, or a user interface;
- changing the six Phase 4 Contract v1 roots to improve experimental results;
- publishing a package, model, image, paper, or repository release without separate approval.

### 2.3 Existing contract rule

The six Phase 4 public Contract v1 roots remain unchanged. Phase 5 research types such as `LabScenario`, `RuntimeConfig`, and `ExecutionRecord` start as Phase 5-owned experimental schemas. They may reference Contract v1 values, but they are not promoted to public Contract v1 until the LangGraph/AutoGen pressure test is complete and a separate compatibility review approves promotion.

## 3. Research questions and hypotheses

### 3.1 Research questions

| ID | Question | Primary evidence |
| --- | --- | --- |
| RQ1 | Can the same IVAD scenario and contracts execute across LangGraph and AutoGen without framework-specific task semantics? | paired framework completion, contract-valid trace rate, incompatibility inventory |
| RQ2 | Does context isolation reduce accepted diagnosis errors relative to same-context self-critique? | B2 vs B3 false-acceptance risk and coverage |
| RQ3 | Does cross-model isolation reduce correlated generator-verifier failure? | B2 vs B4 conditional joint-error rate |
| RQ4 | Are any improvements explained only by abstaining more often? | paired risk-coverage curves and coverage tolerance |
| RQ5 | Does the observed direction replicate outside SupportLab? | OpsLab pilot effect direction and uncertainty |

### 3.2 Hypotheses

- **H1 — framework portability:** both runtime adapters achieve at least 95% contract-valid execution on supported formal scenarios. With LangGraph as the reference implementation, the lower bound of the paired 95% confidence interval for `completion_rate(AutoGen) - completion_rate(LangGraph)` must be greater than `-0.05`.
- **H2 — context isolation:** the upper bound of the paired 95% confidence interval for `risk(B3) - risk(B2)` is below zero without exceeding the frozen coverage-loss tolerance.
- **H3 — model independence:** the upper bound of the paired 95% confidence interval for `risk(B4) - risk(B2)` is below zero, and B4 reduces conditional joint errors on incorrect generator diagnoses.
- **H4 — selective benefit:** a lower false-acceptance risk is not sufficient if it is produced by unacceptable coverage collapse. Every risk result is reported with coverage and a risk-coverage curve.
- **H5 — preliminary domain transfer:** the SupportLab effect direction is checked on OpsLab, but Phase 5 labels it preliminary replication rather than generalization.

Failure to support a hypothesis is a valid Phase 5 result and must remain in the claim ledger.

## 4. Two-stage architecture

### 4.1 End-to-end data flow

```text
LabScenario
  -> ScenarioParityValidator
  -> LangGraphRuntimeAdapter / AutoGenRuntimeAdapter
  -> ExecutionRecord + TraceIR
  -> immutable Trace Corpus
  -> DeepSeek Diagnosis Candidate
  -> frozen Diagnosis Candidate
  -> Verification Matrix B0-B5
  -> Selective Decision
  -> FrameworkLabEvaluator
  -> Artifact Bundle + Paper Tables
```

Stage A is the only stage allowed to execute an agent framework. Stage B consumes immutable serialized inputs and must not invoke LangGraph or AutoGen. In this specification, "offline" means replay-only with respect to the agent and lab environment; Stage B may still call the pinned DeepSeek API or Qwen/vLLM endpoint.

### 4.2 Runtime port

```python
class AgentRuntimeAdapter(Protocol):
    framework_id: str
    framework_version: str

    async def execute(
        self,
        scenario: LabScenario,
        run_config: RuntimeConfig,
    ) -> ExecutionRecord:
        ...
```

The port communicates through Phase 5 research types and existing SpanVouch contracts. Framework-native graph state, messages, group-chat objects, checkpoints, and callbacks cannot cross this boundary.

### 4.3 Runtime adapters

`LangGraphRuntimeAdapter` and `AutoGenRuntimeAdapter` have the same responsibilities:

- materialize the same scenario inputs and tool surface;
- enforce identical step, wall-clock, retry, and tool-call limits;
- capture framework/version/configuration provenance;
- export OpenTelemetry data through the existing Trace mapper;
- return a contract-valid `TraceIR` or a typed execution failure;
- never read gold labels or evaluator-only metadata.

Framework-specific orchestration is permitted. Framework-specific task meaning, tool results, failure severity, or success criteria are not.

### 4.4 Execution record

Each `ExecutionRecord` binds:

- the canonical `TraceIR` hash;
- framework identifier and exact version;
- scenario, template, domain, and failure-family identifiers;
- seed and canonical runtime-config hash;
- tool and environment versions;
- start/end timestamps and latency;
- step and tool-call counts;
- terminal task outcome;
- typed execution/infrastructure/provider error, if any;
- code, package, lockfile, and dataset provenance.

The record cannot contain a gold diagnosis, expected verifier finding, split identity, API credential, raw provider response, prompt text, or hidden reasoning.

### 4.5 Trace corpus

Stage A writes traces and records into an immutable corpus. Each payload is named by content hash, referenced by a corpus manifest, and verified before replay. Stage B rejects missing, unknown, duplicate, or hash-mismatched payloads. A formal trace is never regenerated to repair a Stage B result.

## 5. Lab design

### 5.1 SupportLab

The complete accepted SupportLab becomes the primary controlled domain. Each formal scenario must:

- run in both frameworks;
- use identical inputs, tools, injected failures, seed, timeout, maximum steps, and success predicate;
- preserve existing failure labels and deterministic rules;
- expose any unavoidable framework incompatibility as a typed result;
- forbid framework-specific difficulty reductions or hidden prompt tuning.

SupportLab historical frozen artifacts remain immutable. Phase 5 creates new multi-framework corpus artifacts rather than rewriting Phase 3 or Phase 4 evidence.

### 5.2 OpsLab pilot

OpsLab contains four production-relevant failure families:

1. timeout and retry amplification;
2. rate limiting, resource exhaustion, and graceful degradation;
3. concurrent lock, lease contention, and deadlock;
4. checkpoint, recovery, and workflow-state drift.

Each family contains three failure templates and one no-failure control, for 16 templates total. Every template defines deterministic injection, observable success/failure criteria, required trace evidence, and a framework-parity statement.

OpsLab is a pilot domain in Phase 5. It is not a production operations platform and is not used to claim broad domain generalization.

### 5.3 Scenario parity

`ScenarioParityValidator` rejects a matched pair if any of these differ without an approved typed incompatibility:

- user/task input;
- tool schema or deterministic tool result;
- injected failure and trigger point;
- seed or runtime limit;
- terminal success predicate;
- gold failure family;
- allowed evidence selectors.

Parity failures are reported separately and cannot be silently removed after seeing model results.

## 6. Stage B diagnosis and verification matrix

### 6.1 Diagnosis generation

DeepSeek is the fixed primary diagnosis generator. It consumes only the sanitized `DiagnosticContext` and allowed evidence catalog. Diagnosis prompt, generation configuration, model, provider, usage, and cost provenance are frozen and recorded before formal evaluation.

The generated `DiagnosisReport` is serialized and hashed before verification. Every B0-B5 condition for a trace consumes the same frozen diagnosis bytes.

### 6.2 Conditions

| Condition | Verification mechanism | Context boundary |
| --- | --- | --- |
| B0 | no verifier | every contract-valid diagnosis is accepted |
| B1 | deterministic verifier | Contract and evidence only |
| B2 | DeepSeek self-critique | generator conversation/shared context |
| B3 | DeepSeek verifier | new invocation with isolated provider-visible evidence |
| B4 | Qwen verifier through vLLM | cross-model isolated provider-visible evidence |
| B5 | deterministic verifier plus Qwen | complete SpanVouch composition |

B2 and B3 use the same DeepSeek model identifier, verifier instruction, output schema, sampling configuration, and maximum output-token budget. For B2, the verifier request appends the critique turn to the exact diagnosis-generation message sequence after the diagnosis bytes have been frozen. For B3, a new provider request contains only the frozen diagnosis and the contract-allowed evidence view. This shared-versus-isolated message boundary is the intended causal difference. B4 changes the verifier model and serving endpoint while retaining the B3 isolated evidence view, so it measures an operational cross-model condition rather than a pure model-identity intervention.

Qwen runs through an OpenAI-compatible vLLM endpoint on a rented GPU. The exact model checkpoint, serving image, quantization, generation configuration, and GPU type are locked in the formal experiment manifest. Changing any of them produces a new experiment identity.

### 6.3 Provider-visible boundary

The provider request type cannot contain:

- gold or expected labels;
- mutation metadata;
- dataset split identity;
- framework comparison outcome;
- another condition's verdict;
- raw provider response;
- hidden reasoning.

Evaluator-only joins happen only after every provider call for the cohort returns. Sentinel tests capture serialized provider messages and all pre-call snapshots.

### 6.4 Replay and caching

Stage B requests are keyed by the canonical hash of trace, diagnosis, condition, prompt/version, provider/model, and generation configuration. A repeated request reuses a verified cached result. Cache hits retain original provenance and do not fabricate zero cost; repeated analysis cannot produce another billed call.

## 7. Experimental protocol

### 7.1 Pilot and formal separation

- The pilot uses three repetitions per scenario/framework cell.
- Pilot data is used only to validate the pipeline, estimate variance, select a formal repetition count, and freeze operational limits.
- The formal repetition count is chosen by a recorded power/precision analysis, with a minimum of five and maximum of twenty repetitions per scenario/framework cell.
- Pilot traces, diagnoses, and verifier outputs are excluded from primary formal results.
- Formal labels remain sealed until prompts, conditions, exclusions, and analysis scripts are frozen.

### 7.2 Grouping and pairing

The scenario template, not an individual stochastic repetition, is the primary clustering unit. Comparisons are paired by:

- domain and scenario template;
- failure family;
- framework;
- seed/repetition;
- frozen diagnosis candidate.

No condition may omit a difficult cell that remains in another condition. If a provider or infrastructure error prevents a paired result, the entire pairing status is reported and handled according to the preregistered missingness rule.

### 7.3 Primary endpoint

```text
False Acceptance Risk
= accepted incorrect diagnoses / all accepted diagnoses
```

Risk is never reported without coverage:

```text
Coverage
= accepted diagnoses / all eligible diagnosis candidates
```

The formal coverage-loss tolerance is selected from pilot variance before formal labels are opened and cannot exceed ten percentage points relative to B2.

### 7.4 Secondary endpoints

- selective accuracy;
- full risk-coverage curve;
- failure-family accuracy;
- causal-chain correctness;
- evidence-grounding validity;
- verifier disagreement;
- conditional generator-verifier joint-error rate;
- contract-invalid output rate;
- abstention/review-required rate;
- framework execution and incompatibility rate;
- provider and infrastructure failure rate;
- tokens, billed/estimated cost, GPU time, and latency.

### 7.5 Statistical analysis

- paired cluster bootstrap over scenario templates produces 95% confidence intervals;
- paired binary outcomes receive a McNemar analysis as a secondary check;
- multiple planned condition comparisons use Holm correction;
- SupportLab is the primary domain;
- OpsLab is reported as preliminary replication with its own uncertainty;
- raw repetitions are never treated as independent templates;
- exploratory analyses are visibly separated from preregistered analyses.

### 7.6 Claim gates

An improvement claim for B3 or B4 requires:

1. the paired 95% confidence interval for its false-acceptance risk difference against B2 lies below zero;
2. coverage loss remains within the frozen tolerance;
3. the result is not explained by provider failures, infrastructure failures, or contract-invalid outputs;
4. the effect direction is consistent in both frameworks;
5. every reported number is bound to a reproducible artifact manifest.

OpsLab can strengthen or weaken confidence but cannot by itself establish generalization in Phase 5.

## 8. Failure semantics

Phase 5 uses disjoint failure categories:

| Category | Meaning | Metric treatment |
| --- | --- | --- |
| `framework_execution_failure` | framework cannot complete the scenario | framework reliability, not model accuracy |
| `framework_incompatibility` | matched semantics cannot be represented faithfully | parity inventory, never silent exclusion |
| `infrastructure_failure` | Docker, GPU, storage, or network failure | operational rate and missingness record |
| `provider_failure` | model endpoint fails or returns no usable response | provider reliability and missingness record |
| `contract_invalid` | output violates a required contract | invalid-output rate and safe rejection |
| `diagnosis_error` | diagnosis is wrong under sealed ground truth | diagnosis metric denominator |
| `verification_error` | verifier incorrectly accepts or rejects | verifier/selective metrics |

The first four categories cannot be counted as a correct abstention or silently inserted into a model-accuracy denominator. Raw error bodies and credentials are never persisted.

## 9. Cost and operational control

The monthly experiment budget remains CNY 500-1000.

- pilot paid usage is capped at 10% of the approved experiment budget;
- new paid tasks stop automatically at 80% of the monthly cap;
- Stage A traces are generated once and replayed offline;
- canonical request caching prevents duplicate billing;
- every API call records a request hash and configuration, tokens, cost basis, and model provenance, but not credentials or raw request/response bodies;
- every GPU run records instance type, region/provider, wall time, image, and model checkpoint;
- an incomplete paired matrix cannot be hidden by budget exhaustion;
- paid smoke and formal runs require explicit opt-in; default CI remains zero-provider.

If the budget cannot complete a frozen paired matrix, the formal run pauses. It does not shrink only the expensive or difficult conditions.

## 10. Security, privacy, and leakage controls

- Stage A runtime processes cannot load gold labels.
- Stage B provider workers cannot import evaluator label modules.
- provider-visible request construction is a typed, audited boundary;
- evaluator-only metadata joins occur after calls return;
- trace, prompt, provider, and artifact content passes existing secret classifiers;
- no local environment dump, credential, authorization header, prompt text, raw response, or hidden reasoning enters an artifact bundle;
- gold-bearing formal artifacts have separate access paths from provider inputs;
- every formal split and corpus has a manifest and content hashes;
- exploratory and formal artifacts use different directories and artifact identities.

## 11. Test strategy

### 11.1 Runtime and parity

- `AgentRuntimeAdapter` conformance tests shared by both adapters;
- framework-native unit tests contained inside each adapter;
- paired scenario parity tests;
- timeout, cancellation, retry, and recovery tests;
- contract-valid trace and typed failure tests;
- framework-version provenance tests.

### 11.2 Labs

- deterministic failure-injection tests for every template;
- no-failure control tests;
- evidence-selector and gold-label consistency tests;
- framework-incompatibility tests;
- seed/repetition determinism tests.

### 11.3 Replay and verification

- trace hash/tamper rejection;
- diagnosis byte-identity across conditions;
- B0-B5 matrix completeness;
- shared/isolated context capture tests;
- real evaluator-only sentinel leakage tests;
- request-cache identity and no-rebilling tests;
- provider failure and contract-invalid safe degradation tests.

### 11.4 Evaluation and delivery

- paired-unit and cluster-bootstrap tests on synthetic known effects;
- risk/coverage denominator tests;
- missingness and exclusion-policy tests;
- artifact manifest, bundle, secret, and repeatability tests;
- zero-provider default CI;
- explicit paid smoke gate;
- Docker non-root/persistence/restart/cleanup regression.

## 12. Target module responsibilities

The exact filenames are finalized by the implementation plan, but ownership follows this structure:

```text
src/spanvouch/labs/runtime/           framework-neutral runtime port and research records
src/spanvouch/labs/frameworks/        LangGraph and AutoGen runtime adapters
src/spanvouch/labs/supportlab/        existing SupportLab scenarios/rules
src/spanvouch/labs/opslab/            Phase 5 OpsLab pilot only
src/spanvouch/evaluation/corpus/      corpus manifests, freeze, replay, and tamper checks
src/spanvouch/evaluation/experiments/ matrix planning, execution, caching, and cost control
src/spanvouch/evaluation/statistics/  paired metrics, confidence intervals, and claim gates
```

Production `contracts`, `trace`, `diagnosis`, `verification`, and `review` cannot import any lab/evaluation module. Framework adapters consume the core ports and contracts from the outside.

## 13. Execution batches

### Batch 0 — preregistration and freeze protocol

- write the hypothesis/endpoint/exclusion preregistration;
- define pilot/formal artifact identities;
- add budget and provider-live guards;
- freeze analysis interfaces and claim-ledger rows.

### Batch 1 — multi-framework runtime

- define Phase 5 runtime research types and port;
- adapt the accepted LangGraph path;
- implement the AutoGen adapter;
- pass shared conformance and parity gates.

### Batch 2 — OpsLab pilot

- implement four failure families and sixteen templates;
- validate deterministic injection and no-failure controls;
- execute adapter-level parity without paid formal runs.

### Batch 3 — trace corpus

- execute SupportLab and OpsLab pilot in both frameworks;
- freeze traces, execution records, manifests, labels, and exclusions;
- complete leakage, secret, and parity audits.

### Batch 4 — verification matrix

- freeze DeepSeek diagnosis candidates;
- stand up the pinned Qwen/vLLM endpoint;
- execute B0-B5 with request caching and budget controls;
- produce complete per-condition bundles.

### Batch 5 — statistical and paper assets

- run preregistered analysis;
- generate tables and figures from manifests;
- update the claim-evidence ledger;
- write Method, Experiment Setup, and first Results material;
- publish nothing without separate approval.

## 14. Definition of Done

Phase 5 is complete only when:

1. LangGraph and AutoGen pass the same runtime conformance suite.
2. Every supported formal scenario has an explicit matched-framework parity result.
3. The complete SupportLab and all 16 OpsLab templates produce a frozen multi-framework corpus or a documented typed incompatibility.
4. B0-B5 are complete for every eligible formal cell, with missing conditions reported rather than hidden.
5. Primary and secondary metrics regenerate deterministically from checked-in code and manifest-bound artifacts.
6. Exact confidence intervals, coverage, costs, failures, and exclusions are recorded.
7. Provider calls and GPU runs remain within the approved budget and have complete provenance.
8. Gold, mutation, expected, and split metadata are absent from every provider-visible request and pre-call snapshot.
9. The claim-evidence ledger records supported, unsupported, or unresolved H1-H5 outcomes without exaggeration.
10. Paper Method and Experiment Setup drafts are complete and Results has evidence-backed tables, including null/negative findings if observed.
11. Full tests, strict static checks, architecture guards, zero-provider CI, artifact security, wheel, and Docker gates pass.
12. Conformal, evidence acquisition, CodeLab, human experiments, Repair Agent, and publication remain unimplemented.

## 15. Reviewer-facing claim discipline

Phase 5 may support only the claims directly gated above. In particular:

- framework portability is not framework equivalence;
- an OpsLab directional replication is not broad domain generalization;
- deterministic checks are not evidence of semantic independence;
- cross-model disagreement is not automatically correctness;
- a B4 effect cannot be attributed solely to model identity because the serving stack also changes;
- lower risk without adequate coverage is not an improvement;
- a successful pipeline is not an effectiveness result;
- a null result must not be rewritten as an engineering-only success that hides the research outcome.

The Phase 5 acceptance report must state which hypotheses were supported, contradicted, or unresolved and link each statement to exact artifacts.
