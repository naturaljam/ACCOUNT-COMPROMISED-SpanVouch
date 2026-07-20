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

- Reference bundle manifest SHA-256:
  `607517b359a241a05ef0c5f32df457f10886ffd754d63fa31d5c266037836d5a`
- Reproducible wheel SHA-256:
  `8c08dc35560691166e179ba562898a457da9069d1457c713781c12942888987d`

## Explicit limitations and pending checkpoints

- The branch base does not include Task 17B paper asset generation. The harness uses
  the approved `OfflineAssetEmitter` seam; root integration must rerun with the real
  Task 17B emitter without weakening the E2E.
- Global coverage is 92% (10,822/11,722 statements) on the integrated branch base,
  below the 93% acceptance target. The E2E module itself is 95% covered. Root must
  close the aggregate gap after integrating Task 18A/18B; it is not reported as pass.
- The isolated-worktree provenance test hard-codes `local:phase4-integration`; it is
  excluded only from the coverage command and must be rerun in the integration tree.
- DeepSeek paid pilot, Qwen/vLLM GPU checkpoint, and formal paid matrix remain pending
  explicit spend approvals. This task made zero live provider and zero GPU calls.
