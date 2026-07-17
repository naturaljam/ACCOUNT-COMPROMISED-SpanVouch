# Agent Failure Clinic

Agent Failure Clinic turns failed tool-agent traces into evidence-backed diagnoses and regression artifacts. Phase 1 provides the reproducible SupportLab target agent, TraceIR v1, and 20 frozen traces. Phase 2 adds a deterministic rule diagnoser, an independently evaluated DeepSeek diagnoser, evidence-safe trace projections, and a diagnosis API.

## Requirements

- Python 3.12
- uv 0.8.15 or newer in the 0.8 series
- Docker with Compose v2

## Local verification

```bash
uv sync --frozen --group dev
uv run ruff check src tests
uv run mypy
uv run pytest -v
uv run afc-generate-dataset --output .cache/readme-check --seed 20260715
uv run afc-evaluate-diagnosis --output evals/reports/generated/rules.json
docker compose config --quiet
```

The default `rules` evaluation is deterministic, runs offline, and does not need `DEEPSEEK_API_KEY`.

## Diagnose a trace

Ingest a TraceIR through `POST /v1/traces`, then diagnose the stored trace with:

```text
POST /v1/traces/{trace_id}/diagnoses
```

An empty JSON body selects the offline `rules` diagnoser. To request the optional provider-backed path, send `{"diagnoser":"deepseek"}` and configure the API process with `DEEPSEEK_API_KEY`.

## Controlled DeepSeek evaluation

Use `.env.example` as a local configuration reference, export `DEEPSEEK_API_KEY` in your shell, and start with two allowlisted samples. `DEEPSEEK_MODEL` is optional and defaults to `deepseek-v4-flash`. Never commit or paste the key into logs or chat.

```bash
uv run afc-evaluate-diagnosis \
  --diagnoser deepseek \
  --allow-live-api \
  --run-id invalid_argument-01 \
  --run-id clean-01 \
  --output evals/reports/generated/deepseek-smoke.json
```

`--allow-live-api` is mandatory for DeepSeek mode because the command performs paid external requests. Omit all `--run-id` flags only after inspecting the smoke report to run the full 20-sample experiment. Live reports are generated artifacts and are not committed.

## Run the API and provisioned Phoenix service

```bash
docker compose up --build api phoenix
```

- AFC API: http://localhost:8000
- OpenAPI: http://localhost:8000/docs
- Phoenix: http://localhost:6006

Phase 1 provisions and health-checks Phoenix, but AFC does not yet export its traces to
Phoenix. OTLP exporter wiring and a visible AFC-to-Phoenix trace path belong to a later phase.

## Frozen dataset and labels

`evals/datasets/supportlab-v1` contains 20 deterministic traces: four correct controls and two examples for each of eight fixed failure classes. Phase 2 adds a diagnosis-label sidecar without modifying the Phase 1 traces, labels, or manifest. Both manifests record hashes used to detect unreviewed dataset drift.

## Design documents

- `docs/superpowers/specs/2026-07-15-agent-failure-clinic-design.md`
- `docs/superpowers/specs/2026-07-17-phase2-evidence-diagnosis-mvp-design.md`
- `docs/evaluation/phase2-diagnosis-evaluation.md`
- `docs/research/agent-project-landscape.md`
