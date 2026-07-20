# Phase 5 Offline Reproduction Runbook

## Evidence boundary

This runbook reproduces **offline engineering acceptance** evidence for the
SpanVouch Phase 5 laboratory. It checks SupportLab and OpsLab execution through
LangGraph and AutoGen, Stage A label isolation, the Stage B B0-B5 boundary, and
artifact safety. It is **not paper evidence** and must not be cited as an empirical
DeepSeek/Qwen effectiveness result.

Formal DeepSeek/Qwen evidence: not collected. Cloud GPU: unapproved. Do not set a
provider key, start vLLM, rent hardware, or invoke a live-provider command while
following this runbook.

The separately approved paid workflow starts with `spanvouch experiments
candidates --manifest-only`, obtains approval for that exact credential-free
identity, and only then repeats the command with `--allow-live-provider` and
`--approved-manifest-sha256`. The generated repository is consumed unchanged by
`spanvouch experiments run`. Those commands are documented here as the handoff
boundary only; do not execute them during offline acceptance.

## Preconditions

- Use Python 3.12 and the committed `uv.lock`.
- Start from a clean committed checkout; generated data goes under `.cache/`.
- Keep all provider credentials absent from the shell and repository.
- Do not use `--allow-live-api`.

```powershell
uv sync --frozen --group dev
git status --short
```

`git status --short` must print nothing before release evidence is generated.

## 1. Architecture, secrets, and static quality

```powershell
uv run --no-sync pytest tests/architecture/test_phase5_boundaries.py -v
uv run --no-sync pytest tests/contracts tests/architecture tests/test_delivery_config.py -v
uv run --no-sync ruff check src tests
uv run --no-sync mypy
```

The Phase 5 boundary test proves that production core does not depend on lab or
evaluation packages; Stage A cannot see labels, providers, or statistics; only the
two lab wrappers import their agent frameworks; Stage B imports neither framework;
and checked-in reference artifacts contain no raw model content or credentials.

## 2. Reproduce the Stage A corpus

The following command runs deterministic scripted lab agents. Despite provider
identities present in the experiment policy, it makes no DeepSeek/Qwen call.

```powershell
uv run --no-sync spanvouch labs corpus `
  --mode pilot `
  --config evals/configs/phase5-pilot.json `
  --output-dir .cache/phase5-pilot-corpus
```

Record the printed corpus manifest SHA-256. Never edit a frozen corpus in place.

## 3. Generate sealed labels separately

```powershell
uv run --no-sync spanvouch labs labels `
  --corpus-dir .cache/phase5-pilot-corpus `
  --output-dir .cache/phase5-pilot-labels-sealed
```

The label directory must be a sibling of, not a child of, the corpus. Provider
runners cannot import or open it. Joining labels is a post-call evaluator action.

## 4. Offline regression suite

```powershell
uv run --no-sync pytest tests/labs tests/evaluation/corpus `
  tests/evaluation/experiments tests/evaluation/statistics -v
```

Run the network-disabled E2E and reproduce the committed reference bundle:

```powershell
uv run --no-sync pytest tests/evaluation/test_phase5_offline_e2e.py -v
uv run --no-sync python -m spanvouch.evaluation.offline_acceptance `
  --output-dir .cache/phase5-offline-smoke
```

Compare `config.json`, `metrics.json`, `structured-events.jsonl`, `README.md`, and
their manifest payload digests byte-for-byte. The committed `manifest.json` file
has SHA-256
`7bfd56f2b190233d58db4e6f7651005e81e2c9fd7427d108d411cc48012811ae`.
The accepted bundle was generated from clean code-under-test commit
`368a745ba10b04f46e8bdf67788eba2395a5b0d5`. The command refuses a dirty Git
worktree; tests may inject an explicit deterministic fixture identity only for
non-accepted reproducibility checks.

## 5. Verify the delivery gates

Run the full offline suite, coverage, wheel, and Docker gates:

```powershell
uv run --no-sync pytest --cov=spanvouch --cov-report=term-missing
uv build --wheel --build-constraints build-constraints.txt `
  --require-hashes --no-cache
docker compose config --quiet
docker compose build api
docker compose up --detach --wait --wait-timeout 90 api
docker compose exec -T api sh -c 'test "$(id -u):$(id -g)" = "10001:10001"'
docker compose restart api
docker compose down --volumes --remove-orphans
```

The final integrated baseline reported 1,640 passing tests, 1 skipped test, and
93.42% coverage. The repeated wheel, non-root container, health, writable
persistence, restart, teardown, and no-residue checks passed.

## 6. Interpret the result

A green run establishes only implementation properties. It does not support H1-H5
and does not justify portability equivalence, verifier correctness from
disagreement, or generalization beyond OpsLab. Formal claims require the frozen,
approved paid matrix and its independently verified analysis manifest.

Any paid pilot is a separate checkpoint requiring the user to approve exact model
and image revisions, GPU quote, call/token budget, maximum CNY spend, shutdown
command, and rollback. Approval of this offline runbook is not spend approval.
Follow the [live experiment preparation
checklist](../research/phase5-live-experiment-preparation-2026-07-20.md) before
requesting that approval.

For any approved live checkpoint, set `SPANVOUCH_PHASE5_BUDGET_LEDGER_PATH` to
one stable absolute path shared by candidate, pilot, formal, and resumed runs.
Set `SPANVOUCH_PHASE5_GPU_LEASE_PATH` to an absolute canonical-JSON lease file
only for the Qwen matrix path. Candidate generation is DeepSeek-only and does
not require that GPU variable. Matrix caches remain manifest-scoped; the global
ledger records an approved lease once, treats identical lease ID/content as an
idempotent replay, and rejects conflicting content before provider composition.
