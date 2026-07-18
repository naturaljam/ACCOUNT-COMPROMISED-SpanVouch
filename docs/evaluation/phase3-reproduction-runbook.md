# Phase 3 Reproduction Runbook

This Phase 4-owned companion contains executable reproduction procedures for the accepted Phase 3 evidence. The historical record at `docs/evaluation/phase3-verification-review.md` remains immutable and has SHA-256 `b67345396cfd86a7eb96db98b76834dee057983e8f38491c59c2376fc2bb2e74`.

## Environment

Run commands from the repository root after synchronizing the frozen development environment:

```powershell
uv sync --frozen --group dev
```

All gates below are offline and must run without `DEEPSEEK_API_KEY` or `--allow-live-api`.

## Twenty-process SQLite stability gate

On Windows, run exactly:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/review/test_sqlite_process_stability.py -q
```

The test starts all 20 independent Python interpreters before waiting for any of them. Each process opens the same SQLite database, creates a distinct deterministic review case through `SQLiteReviewRepository`, reopens the repository, and emits a sanitized result. The parent collects every exit code plus stdout/stderr, fails on any process error, and reopens the database to verify 20 distinct durable cases with no verifier reports or provider path.

## Docker, restart, non-root, and persistence gate

The authoritative executable procedure is the `Smoke-test containerized API` step in `.github/workflows/ci.yml`. Run that step's `run: |` Bash block unchanged from the repository root on its pinned `ubuntu-24.04` CI environment, after the workflow's frozen sync, wheel build/install, lint, type, test, dataset, and deterministic-evaluation steps.

Before the smoke block, the workflow runs exactly:

```text
docker compose config --quiet
docker compose build api
```

The named smoke block then performs the accepted gate end to end: healthy Compose startup; exact `/health` response; runtime and `/data` ownership `10001:10001`; frozen-trace ingest; deterministic `afc-review` create and confirm; API restart with the same named volume; byte-exact terminal detail comparison; one revision, one deterministic verifier report, and five ordered events; and destructive volume cleanup only after the recovery assertions pass. Its failure trap preserves the volume long enough to print container state and logs. The workflow contains neither a provider key nor `--allow-live-api`, so the gate makes zero provider calls.

`tests/test_delivery_config.py` guards both this runbook pointer and the required workflow assertions so the prose pointer and executable CI procedure cannot silently diverge.
