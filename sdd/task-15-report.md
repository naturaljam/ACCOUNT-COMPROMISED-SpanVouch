# Task 15 SDD Report

## Outcome

Implemented the label-isolated Phase 5 provider runner and the separate offline
post-call gold join.

## Delivered contracts

- `ExperimentRunner.run_provider_phase` accounts for every frozen plan as
  completed, typed failed, not invoked by policy, or paused.
- Paid framework pairs are admitted together; a pause stops the remaining pair
  before another call.
- Every completed call is published immediately under its exact plan ID with
  atomic no-replace semantics, enabling verified resume without rebilling.
- `ProviderPhaseManifest` binds config, corpus, candidates, matrix, plan order,
  result hashes, status/missingness, usage, cost, and completion state without
  gold metadata.
- `PostCallEvaluator.join` verifies a complete provider phase, trusted sealed
  labels, exact corpus cell equality, and record/trace bindings before writing
  family/control/split/correctness to a separate immutable directory.
- CLI routing exposes `spanvouch experiments run` and
  `spanvouch experiments evaluate` with disjoint argument surfaces.

## TDD and review

- RED began with three missing-module collection failures.
- GREEN focused suite: 37 passed.
- Added process-import isolation and sentinel path/open/serialized-byte tests.
- Added failure, cancellation, pair-budget, pair-pause, interruption/resume,
  trusted-hash, exact-cell-set, and CLI boundary coverage.
- Self-review fixed Windows long staging paths, exact existing-manifest parent
  identity verification, immediate per-call publication, and complete
  missingness accounting.

## Integration note

This branch intentionally uses the minimal injected `ConditionExecutor`
protocol because Task 14 was not present at its base. Controller integration
can supply the Task 14 executor without changing the runner's label-isolated
public boundary.
