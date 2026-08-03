# DeepSeek-Only Formal Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** Permit and execute a formal Phase 5 run whose paid semantic conditions are DeepSeek B2/B3 only, while B4/B5 remain explicitly policy-skipped and all artifacts remain auditable.

**Architecture:** Reuse the existing complete B0-B5 matrix identity and provider repository. Extend the existing `deepseek_only` composition path from pilot-only to an explicit formal opt-in, preserving Qwen absence and recording B4/B5 as `not_invoked_by_policy`; freeze a tracked five-repetition formal configuration before generating the formal corpus and candidates.

**Tech Stack:** Python 3.12, Pydantic, pytest, uv, Git content-addressed artifact repositories, DeepSeek managed API.

## Global Constraints

- Formal repetitions: exactly 5, the minimum permitted by `evals/configs/phase5-formal-policy.json`.
- Formal coverage-loss tolerance: `0.05`, within the policy maximum `0.10`.
- B0/B1 remain deterministic/offline; B2/B3 use DeepSeek; B4/B5 are `not_invoked_by_policy`.
- Never read or construct Qwen runtime for this run; never expose credentials or raw request IDs.
- Formal configuration must be tracked and byte-identical to the commit used for corpus generation.
- No remote push, tag, paper, or README update in this execution.

---

### Task 1: Formal DeepSeek-only runtime boundary

**Files:**
- Modify: `src/spanvouch/evaluation/run_phase5_matrix.py`
- Test: `tests/evaluation/test_run_phase5_matrix.py`

- [x] Add a regression test showing an explicitly requested formal DeepSeek-only run is accepted past the pilot-only guard.
- [x] Remove the pilot-only rejection while retaining the explicit `--deepseek-only` opt-in and all existing authorization/hash checks.
- [x] Verify the targeted matrix/live tests, Ruff, and mypy.

### Task 2: Freeze and commit formal configuration

**Files:**
- Create: `evals/configs/phase5-formal-deepseek-only.json`
- Modify: local Git history only; no remote push.

- [x] Freeze the pilot configuration at UTC `2026-08-03T00:00:00Z`, repetitions `5`, and coverage tolerance `0.05` using `freeze_formal_config`.
- [x] Verify the self-hashed configuration and commit all code/tests/config needed for reproducibility.

### Task 3: Generate formal corpus and DeepSeek candidates

**Files:**
- Create: `.cache/phase5/formal-corpus`
- Create: `.cache/phase5/formal-deepseek-candidates`

- [ ] Generate and verify five-repetition corpus with `spanvouch labs corpus --mode formal`.
- [ ] Generate DeepSeek diagnosis candidates with the formal config and exact candidate manifest approval.
- [ ] Verify candidate coverage, secret scans, cache audits, and zero active reservations before matrix planning.

### Task 4: Run and verify formal matrix

**Files:**
- Create: `.cache/phase5/formal-deepseek-only-<matrix-prefix>`

- [ ] Compute and record the exact formal matrix manifest SHA-256.
- [ ] Run `spanvouch experiments run --formal-run --allow-live-provider --deepseek-only` with the exact approved hash.
- [ ] Verify 5 repetitions, B2/B3 DeepSeek completion, B4/B5 policy skips, no Qwen artifacts, cost, stop events, reservations, and provider manifest integrity.

### Task 5: Final verification and handoff

- [ ] Run focused tests, Ruff, mypy, and final artifact verification.
- [ ] Report local paths, hashes, cost, and any residual test/environment gaps.
- [ ] Remain standby; do not publish remotely without separate approval.
