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

If an offline reference bundle is added by the separate end-to-end acceptance
task, regenerate it twice and compare `config.json`, `metrics.json`,
`structured-events.jsonl`, and `README.md` byte-for-byte. Verify every payload
digest against `manifest.json`. No Phase 5 reference bundle is claimed by this
architecture/documentation task.

## 5. Interpret the result

A green run establishes only implementation properties. It does not support H1-H5
and does not justify portability equivalence, verifier correctness from
disagreement, or generalization beyond OpsLab. Formal claims require the frozen,
approved paid matrix and its independently verified analysis manifest.

Any paid pilot is a separate checkpoint requiring the user to approve exact model
and image revisions, GPU quote, call/token budget, maximum CNY spend, shutdown
command, and rollback. Approval of this offline runbook is not spend approval.
