# Task 18B SDD Report

## Delivered

- Added a deterministic zero-provider Phase 5 harness with a Task 17B asset-emitter
  protocol seam.
- Executed SupportLab and OpsLab through both native lab adapters, normalized only
  nondeterministic trace identity/timing, froze and replayed four records, froze four
  fake diagnoses, planned and ran all 24 B0-B5 cells, joined sealed labels only after
  provider-phase completion, and computed risk/coverage, paired bootstrap, McNemar,
  and Holm statistics.
- Patched socket and httpx transports to fail on any network attempt. The two E2E
  runs produced identical logical manifests and byte-identical bundles.
- Committed the six-file fake-provider reference bundle with explicit wording that
  it is engineering evidence and not paper evidence.
- Completed wheel reproducibility and the Docker non-root, writable persistence,
  restart, health, teardown, and no-residue checks.

## Evidence identities

- Reference bundle `manifest.json` file SHA-256:
  `7bfd56f2b190233d58db4e6f7651005e81e2c9fd7427d108d411cc48012811ae`
- Reproducible wheel SHA-256:
  `8c08dc35560691166e179ba562898a457da9069d1457c713781c12942888987d`

## Integration addendum

- Task 17B paper asset generation is integrated. The offline E2E uses the approved
  asset-emitter boundary and the committed bundle reproduces byte-for-byte.
- The final integrated full suite reported 1,640 passing tests, 1 skipped test,
  and 93.42% coverage. This closes the earlier 92% historical gap.
- The reference `manifest.json` file now has SHA-256
  `7bfd56f2b190233d58db4e6f7651005e81e2c9fd7427d108d411cc48012811ae`,
  generated from clean code-under-test commit
  `368a745ba10b04f46e8bdf67788eba2395a5b0d5`.
- DeepSeek paid pilot, Qwen/vLLM GPU checkpoint, and the formal paid matrix still
  require explicit spend approval. Offline acceptance made zero live provider and
  zero GPU calls.
