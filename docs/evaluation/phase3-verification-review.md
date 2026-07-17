# Phase 3 Verification and Human Review Evaluation

Updated: 2026-07-18

## Scope and provenance

This document records the reproducible evidence for the Phase 3 `diagnosis -> independent verification -> at most one evidence revision -> human decision` workflow. The Task 12 verification baseline is commit `578df55fc4cac36a8f522ce9e03c3c7f4111d117`; the delivery delta is the single commit titled `chore: complete phase 3 delivery gates`. Git records the resulting commit SHA because a commit cannot embed its own hash without changing that hash.

The final acceptance delta is one additional commit titled `fix: close phase 3 acceptance gaps`. It adds a nonzero deterministic acceptance gate, explicit paid-resume consent, durable verifier-to-report hash bindings, and atomic current-revision verifier pointers. Git likewise records the resulting commit SHA outside this self-referential document.

The frozen review cohort contains 36 candidates: 20 unmodified rule reports and 16 deterministic mutations. It is bound to the frozen 20-trace SupportLab cohort.

| Artifact | SHA-256 |
|---|---|
| `supportlab-review-v1/manifest.json` | `0a1261d716f930764fa5abd01e6da1b7772278b9da95a4bd7ff465757a38a231` |
| `review-candidates-v1.jsonl` | `6fb084b3981d044159a18b33034cebf4f946af7ab27d02b4fc51bbb566faf08b` |
| `review-labels-v1.jsonl` | `d41a87247456264863d70f807256a5d1b6f24ab84422dc406a92ef867e36b305` |
| source `supportlab-v1/manifest.json` | `b14eac192e7b683fb908f2f7f54efccb31ab100bf19563476b824d192060cb38` |

The review manifest records seed `20260717`, generator `supportlab-review-generator-v1`, `valid_count=20`, and `mutation_count=16`.

## Deterministic evaluation

The offline evaluator is the CI and release gate:

```bash
uv run afc-generate-review-dataset --output .cache/phase3-review-dataset --seed 20260717
uv run afc-evaluate-review --output .cache/phase3-review-a.json
uv run afc-evaluate-review --output .cache/phase3-review-b.json
```

The acceptance evidence is:

| Requirement | Recorded result |
|---|---:|
| Valid reports accepted | `20/20` |
| Injected deterministic defects detected exactly | `16/16` |
| Unsupported forced diagnoses blocked | `6/6` |
| Valid false-block rate | `0/20` |
| Structured deterministic outputs | `36/36` |
| Operational errors | `0/36` |
| Repeated report bytes | exact |

The evaluator joins candidate IDs to expected finding codes; it does not infer success from aggregate counts alone. Selector, observed-value, hash, claim grounding, critical-span grounding, scope guard, and invariant behavior are also exercised by focused unit tests.

The command exits nonzero and records `status=failed` if any exact deterministic gate degrades, even when two degraded reports are byte-identical. Hybrid semantic verdicts remain descriptive measurements without an accuracy threshold.

## Persistence and delivery evidence

Task 12 runs the following offline gates before any paid experiment:

- frozen `uv sync`, Ruff, strict mypy, and coverage-enabled full pytest;
- Phase 1 dataset regeneration and manifest equality;
- two byte-identical deterministic diagnosis evaluations;
- Phase 3 review dataset regeneration and three bound-hash comparisons;
- two byte-identical deterministic review evaluations;
- pinned Docker image build and healthy Compose startup;
- a real API ingest plus `afc-review` create/show/confirm flow;
- API-container restart with the same named `afc_data` volume and byte-identical terminal GET;
- runtime UID/GID `10001:10001`, writable `/data`, and `/data` ownership `10001:10001`;
- cleanup with `docker compose down --volumes --remove-orphans` after recovery is proven.

The 2026-07-17 offline gate produced the following fresh evidence:

- `uv sync --frozen --group dev`: 60 packages audited;
- Ruff: clean;
- strict mypy: 63 source files, no issues;
- pytest with coverage: 485 passed at 93% total coverage; after the final whole-branch review fixes and follow-up hardening, the fresh repository suite passed 554 tests, with only the pre-existing Starlette/httpx deprecation warning;
- deterministic review report: `status=complete`, 36 candidates, all six recorded quality rates at their accepted value, 0 operational errors, and 0 provider tokens;
- Docker image build and health check: passed;
- real persisted case: one revision, one deterministic verifier report, five ordered events, terminal `confirm` decision;
- post-restart CLI GET: byte-identical to the pre-restart terminal detail;
- container runtime and data ownership: UID/GID `10001:10001`; `/data/afc.db` owned by `10001:10001` and writable;
- isolated named volume cleanup: passed after the recovery assertion.

On this Windows workstation, `uv run pytest` resolves the test runner through a bundled runtime that cannot import the repository's `tests` namespace. The canonical `.venv\Scripts\python -m pytest --cov=afc --cov-report=term-missing` invocation uses the frozen synced environment and produced the passing full-suite evidence above. Linux CI continues to run the locked wheel installation with `uv run --no-sync`.

No live result is inferred from these offline checks.

## Security and recovery interpretation

The default API, CLI, evaluator, and CI paths are `rules + deterministic` and make zero external model calls. Secret-hygiene tests inject a sentinel credential and raw provider body, then check the sanitized workflow exception, verifier report, SQLite rows, workflow events, API JSON, and CLI JSON. Docker build-context tests exclude `.env`, `.env.*`, `.data/`, caches, and generated live reports while explicitly allowing the empty `.env.example` template.

SQLite stores only the recursively sanitized, allowlisted diagnostic snapshot and structured audit records. It does not store raw TraceIR, prompts, keys, authorization headers, hidden reasoning, or raw provider responses. Active semantic-verification and evidence-revision leases are owner-fenced and renewed below their expiry interval, so normal provider latency cannot start a concurrent second call. After a genuinely dead worker stops heartbeating, the stale lease can still be reclaimed; crash recovery remains at-least-once while persisted domain effects remain deduplicated.

`reviewer_label` is an audit label supplied by the caller; it is not authentication. Phase 3 does not claim auth or RBAC.

## Controlled live semantic comparison

Live semantic verification is not a CI gate and requires explicit authorization plus `--allow-live-api`. The procedure is:

1. run one unmodified supported candidate and one mutated or unsupported candidate;
2. inspect strict-schema success, input independence, verdicts, tokens, latency, and errors;
3. only if the smoke is structurally valid, run all 36 candidates;
4. record aggregate verdict distribution, deterministic/semantic disagreement, structured-output success, operational errors, token totals, p50/p95 latency, and cost only when pricing is configured.

The controlled experiment was explicitly authorized and run on 2026-07-17 with `deepseek-v4-flash`. The two-case smoke used `invalid_argument-01--unmodified` and `context_corruption-01--unsupported_scope`:

| Smoke evidence | Result |
|---|---:|
| Semantic attempts / strict outputs | `2 / 2` |
| Verdict distribution | `verified=1`, `needs_evidence=1` |
| Operational errors | `0` |
| Deterministic/semantic disagreement | `0.5000` |
| Input / output / total tokens | `3,291 / 258 / 3,549` |
| Latency p50 / p95 | `1,682.16 ms / 1,909.56 ms` |

The valid supported report was `verified`. The unsupported forced-classification mutation received `needs_evidence`; this is measured disagreement, not a claimed semantic accuracy failure. Both reports carried model, prompt fingerprint, token, and latency provenance. The generated report contained no key, authorization header, prompt body, hidden reasoning, or raw provider body.

After the smoke passed its structural gate, the 36-candidate comparison produced:

| Full comparison evidence | Result |
|---|---:|
| Candidate-level semantic reports | `36 / 36` |
| Provider calls | `34` |
| Strict structured outputs | `29 / 36` (`0.8056`) |
| `invalid_verifier_output` protections | `7 / 36` |
| Operational-error rate | `0.0000` |
| Verdict distribution | `verified=18`, `needs_evidence=11`, `review_required=7` |
| Disagreement rate on strict outputs | `0.5517` |
| Input / output / total tokens | `54,663 / 3,983 / 58,646` |
| Latency p50 / p95 | `1,460.84 ms / 2,680.61 ms` |
| Estimated cost | not reported; pricing was not configured |

Two invalid-selector mutations failed local semantic preflight without contacting the provider. Five additional provider responses did not satisfy the strict verifier schema and were converted to safe `review_required/invalid_verifier_output` reports. Thus `status=complete` means there were no operational provider failures; it does **not** mean every provider output passed the strict schema.

The deterministic gates remained unchanged at valid pass rate `1.0`, hard-defect recall `1.0`, and unsupported-scope detection `1.0`. No semantic accuracy threshold is claimed from this cohort. The local generated reports are `evals/reports/generated/phase3-semantic-smoke-2.json` and `evals/reports/generated/phase3-semantic-full-36.json`; both remain ignored and uncommitted.
