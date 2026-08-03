# SpanVouch v0.5.0

SpanVouch v0.5.0 packages the completed DeepSeek-only formal engineering evaluation and its reproducible offline handoff.

## Release contents

- Adds `spanvouch experiments prepare-analysis`, which verifies evaluation and provider repositories before producing canonical `analysis-input.json` and a bound manifest.
- Records 2,148 formal plans, 358 eligible cells, and two explicitly ineligible cells without silently dropping them.
- Runs B0-B3 to completion with DeepSeek and records B4/B5 as `not_invoked_by_policy`; no Qwen requests or results are included.
- Preserves deterministic bootstrap intervals, artifact hashes, budget accounting, and fail-closed H1-H5 claim gates.

## Reproduce the analysis handoff

```text
uv run spanvouch experiments prepare-analysis \
  --evaluated-results .cache/phase5/formal-evaluated \
  --provider-results .cache/phase5/formal-matrix-run-<manifest> \
  --config .cache/phase5/formal-analysis-config.json \
  --output-dir .cache/phase5/formal-analysis-input
```

The command is offline-only and never invokes a provider. The generated directory contains only canonical JSON inputs and a manifest binding their SHA-256 identity. Published analysis assets remain fail-closed when a cross-model condition is policy-skipped.
