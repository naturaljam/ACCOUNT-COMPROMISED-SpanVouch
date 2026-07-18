# Phase 2 Evidence Diagnosis Evaluation

Updated: 2026-07-17

## Purpose

This evaluation checks whether a diagnoser can classify a frozen SupportLab trace, identify its critical span, and ground each causal claim in selectors that resolve against the label-safe diagnostic view. It evaluates the deterministic rule diagnoser and the independent DeepSeek diagnoser through the same `DiagnosisReport` contract.

The frozen cohort contains 20 traces:

- 10 supported failures: two traces for each of five supported failure types;
- 4 clean controls;
- 6 unsupported failures that must produce a semantic abstention.

The Phase 2 gold labels live in `evals/datasets/supportlab-v1/diagnosis-labels-v1.jsonl`. They are a sidecar: the Phase 1 fixtures remain unchanged.

## Offline rule evaluation

No API key or network access is required.

```bash
uv run afc-evaluate-diagnosis \
  --output evals/reports/generated/rules.json
```

Run the command twice and compare SHA-256 hashes. The artifacts must be byte-identical. The hard acceptance gates are:

- supported classification accuracy: `1.0`;
- critical-span Top-1 accuracy: `1.0`;
- clean false-positive rate: `0.0`;
- unsupported abstain rate: `1.0`;
- evidence-selector validity: `1.0`;
- gold-evidence hit rate: `1.0`;
- operational error rate: `0.0`.

Weak baselines are reported only for context. They do not emit evidence and therefore do not participate in evidence metrics.

## Controlled live evaluation

Live evaluation is deliberately outside CI and requires both an environment key and the explicit network permission flag. Set `DEEPSEEK_API_KEY` in the local shell; do not pass it as a command argument, commit it, or paste it into chat.

Start with two samples:

```bash
uv run afc-evaluate-diagnosis \
  --diagnoser deepseek \
  --allow-live-api \
  --run-id invalid_argument-01 \
  --run-id clean-01 \
  --output evals/reports/generated/deepseek-smoke.json
```

Before a full run, inspect that:

- every successful result validates against the diagnosis schema;
- every evidence selector resolves locally against the diagnostic trace view;
- provider errors appear as stable operational-error types rather than fabricated diagnoses;
- token counts, request latency, model, prompt fingerprint, and request ID are present;
- no prompt, raw provider response, key, or authorization header is stored.

Only after the smoke is structurally sound should the operator omit `--run-id` to run all 20 samples. The small smoke report is not a substitute for the full comparison. No artificial DeepSeek accuracy threshold is claimed from this small dataset.

## Recorded 20-trace experiment

The controlled experiment on 2026-07-17 used `deepseek-v4-flash` in non-thinking JSON mode with prompt version `diagnosis-v1`. It ran sequentially over the same 20 frozen traces as the rule baseline.

| Metric | Evidence rules | DeepSeek |
|---|---:|---:|
| Supported accuracy | 1.000 | 0.857 |
| Critical-span Top-1 | 1.000 | 0.800 |
| Evidence-selector validity | 1.000 | 1.000 |
| Gold-evidence hit rate | 1.000 | 1.000 |
| Evidence precision | 1.000 | 0.247 |
| Clean false-positive rate | 0.000 | 0.000 |
| Unsupported abstain rate | 1.000 | 0.000 |
| Coverage | 0.700 | 1.000 |
| Structured-output success | 1.000 | 1.000 |
| Operational-error rate | 0.000 | 0.000 |

DeepSeek correctly handled all four clean controls and four of the five supported failure families. Both `policy_violation` traces were classified as `invalid_argument`, producing `12/14` correct supported decisions. On both loop traces it selected the root span instead of the final repeated tool span, producing `8/10` critical-span Top-1.

The strongest negative result is scope control: all six unsupported traces were forced into supported diagnoses or `no_failure`, so the unsupported abstain rate was `0/6`. Evidence remained mechanically safe because every selector was resolved locally, but the model selected more evidence than the gold set: 95 refs across 20 samples, with an average of 4.75 refs per sample and evidence precision of 0.247.

Provider usage was 31,557 input tokens, 4,101 output tokens, and 35,658 total tokens. Measured request latency was 2,044 ms p50 and 2,888 ms p95. Using the published cache-miss prices on the run date, the conservative cost estimate was USD 0.005566; the report leaves `estimated_cost_usd` null because the provider response did not expose enough cache-hit detail for an exact calculation. Pricing is time-sensitive; consult the [official DeepSeek pricing page](https://api-docs.deepseek.com/quick_start/pricing) before repeating the experiment.

The first two-sample smoke also proved the strict-output guard was effective: an underspecified prompt produced valid JSON with natural-language enum/scalar values and was rejected as `invalid_model_output`. A TDD fix made the exact JSON enum and numeric contract explicit; the repeated smoke then correctly returned `invalid_argument` and `no_failure` before the full run was authorized.

## Reading the report

`status=complete` means every sample produced a semantic report without an operational provider failure; `status=partial` means at least one provider operation failed. A semantic `abstained` decision is distinct from an operational failure. `coverage` excludes semantic abstentions. `structured_output_success_rate` counts schema-valid results, including valid semantic abstentions, but excludes `invalid_model_output` guard results.

Generated reports are written under `evals/reports/generated/` and intentionally ignored by Git because live artifacts contain provider metadata and are not deterministic evidence for the repository.
