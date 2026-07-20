# Phase 4 Reproducibility

Release evidence is a clean-worktree artifact. Create it only from a committed
candidate with `dirty_worktree=false`; exploratory dirty output is explicitly
non-release evidence. The reference bundle has `manifest.json`, `config.json`,
`metrics.json`, `environment.txt`, and `README.md`. Its manifest binds the
code commit, lockfile/runtime, contract versions, dataset hashes, configuration
hash, output hashes, provider metadata, usage, and cost semantics.

The frozen SupportLab and review datasets are regenerated from their recorded
seeds and compared by manifest/payload hash. Old AFC artifacts remain immutable
and are linked as parent provenance rather than rewritten. Default commands use
the deterministic offline provider state: `provider_status=not_used`, zero
requests, zero input/output tokens, and no cost object. A live provider requires
an explicit opt-in and must record sanitized provider metadata, never raw
responses or credentials.

Gold labels, mutation metadata, expected findings, and split identity are
excluded only from provider-visible, pre-call inputs/messages/snapshots.
Provider-visible data and evaluator label joins use separate interfaces; the
evaluator joins labels only after all verifier calls return. Post-call
`metrics.json` may include `mutation_kind` and candidate ID for analysis, but it
must not include gold labels, expected findings, or split secrets unless a later
approval explicitly permits them. The checked-in reference contains mutation
metadata only in `metrics.json`.

API keys, authorization headers, prompt text, hidden reasoning, raw provider
bodies, and local environment values are excluded from the entire bundle. Live
provider metadata is sanitized before persistence; raw responses and credentials
are never recorded.

From a clean release candidate, reproduce the checked-in reference evidence:

```powershell
uv run spanvouch evaluate review --output .cache/phase4-reproduction/metrics.json --bundle-dir .cache/phase4-reproduction/bundle --artifact-id phase4-offline-reference
```

Compare `.cache/phase4-reproduction/metrics.json` with
`evals/reports/reference/phase4-offline-bundle/metrics.json` byte-for-byte and
compare the generated manifest hash with the published reference manifest.

## Phase 5 evidence classes

Phase 5 maintains two evidence classes that must never be conflated:

1. **Offline engineering acceptance** covers deterministic lab execution,
   LangGraph/AutoGen parity, Stage A/Stage B separation, label isolation, B0-B5
   plumbing, architecture tests, and sanitized reproducible artifacts.
2. **Formal research evidence** requires an explicitly approved, frozen run using
   the registered DeepSeek generator/verifiers and isolated Qwen verifier. It must
   include complete paired cells, provider/GPU provenance, cost records, post-call
   label joining, preregistered statistics, and claim-gate outputs.

Offline fixtures and fake-provider results are not paper evidence. Formal
DeepSeek/Qwen evidence: not collected. Cloud GPU: unapproved. H1-H5 therefore stay
planned or unresolved; no documentation gate can advance the claim ledger.

The exact offline sequence and hash checks are in
`docs/evaluation/phase5-reproduction-runbook.md`. Current completion criteria and
explicitly missing evidence are in `docs/evaluation/phase5-acceptance.md`.
