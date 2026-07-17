# Agent Failure Clinic

Agent Failure Clinic turns failed tool-agent traces into evidence-backed diagnoses and regression artifacts. Phase 1 provides the reproducible SupportLab target agent, TraceIR v1, 20 labeled traces, weak baselines, and a trace ingestion API.

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
docker compose config --quiet
```

## Run the API and provisioned Phoenix service

```bash
docker compose up --build api phoenix
```

- AFC API: http://localhost:8000
- OpenAPI: http://localhost:8000/docs
- Phoenix: http://localhost:6006

Phase 1 provisions and health-checks Phoenix, but AFC does not yet export its traces to
Phoenix. OTLP exporter wiring and a visible AFC-to-Phoenix trace path belong to a later phase.

## Phase 1 dataset

`evals/datasets/supportlab-v1` contains 20 deterministic traces: four correct controls and two examples for each of the eight fixed failure classes. `manifest.json` records hashes used by CI to detect unreviewed dataset drift.

## Design documents

- `docs/superpowers/specs/2026-07-15-agent-failure-clinic-design.md`
- `docs/research/agent-project-landscape.md`
