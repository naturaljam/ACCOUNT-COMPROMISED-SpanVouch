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

## Reading the report

`status=complete` means every sample produced a schema-valid semantic report; `status=partial` means at least one provider operation failed. A semantic `abstained` decision is a valid report and is distinct from an operational failure. `coverage` excludes semantic abstentions, while `structured_output_success_rate` counts all valid reports, including abstentions.

Generated reports are written under `evals/reports/generated/` and intentionally ignored by Git because live artifacts contain provider metadata and are not deterministic evidence for the repository.
