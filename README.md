# Agent Failure Clinic

Agent Failure Clinic turns failed tool-agent traces into evidence-backed, independently verified diagnoses. Phase 1 provides the reproducible SupportLab target agent, TraceIR v1, and 20 frozen traces. Phase 2 adds deterministic and explicitly enabled DeepSeek diagnosis. Phase 3 adds deterministic and optional semantic verification, a one-revision bound, SQLite recovery, and mandatory human `confirm`, `correct`, or `reject` decisions through API and CLI.

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
uv run afc-generate-review-dataset --output .cache/review-check --seed 20260717
uv run afc-evaluate-review --output evals/reports/generated/review-rules.json
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

## Verify and review a diagnosis offline

The default workflow is `rules + deterministic`: it performs no external model call. Start the API with a local SQLite database in one terminal:

```bash
export AFC_DB_PATH=.data/afc.db
uv run uvicorn afc.api.app:app --host 127.0.0.1 --port 8000
```

In another terminal, extract the first checked-in frozen trace, ingest it through `POST /v1/traces`, and capture the returned `trace_id`. The review create response supplies the `case_id` and optimistic-lock `version` consumed by show and confirm:

```bash
mkdir -p .cache
python -c 'from pathlib import Path; source=Path("evals/datasets/supportlab-v1/traces.jsonl"); Path(".cache/afc-demo-trace.json").write_text(source.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")'

trace_id="$(
  curl --fail --silent --show-error \
    -H 'content-type: application/json' \
    --data-binary @.cache/afc-demo-trace.json \
    http://127.0.0.1:8000/v1/traces \
  | python -c 'import json,sys; print(json.load(sys.stdin)["trace_id"])'
)"

created="$(uv run afc-review create \
  --trace-id "$trace_id" \
  --diagnoser rules \
  --verifier deterministic \
  --idempotency-key demo-create-001)"

case_id="$(python -c 'import json,sys; print(json.loads(sys.argv[1])["case"]["case_id"])' "$created")"
version="$(python -c 'import json,sys; print(json.loads(sys.argv[1])["case"]["version"])' "$created")"

uv run afc-review show --case-id "$case_id"

uv run afc-review decide \
  --case-id "$case_id" \
  --action confirm \
  --expected-version "$version" \
  --reviewer-label local-reviewer \
  --idempotency-key demo-decision-001
```

The equivalent API endpoints are:

```text
POST /v1/traces/{trace_id}/diagnosis-reviews
GET  /v1/diagnosis-reviews/{case_id}
POST /v1/diagnosis-reviews/{case_id}/resume
POST /v1/diagnosis-reviews/{case_id}/decisions
```

Every verification result reaches human review; a verifier never grants release authority. A revision-capable diagnoser may receive one evidence-gap revision request, never two. `confirm` accepts the current report, `correct` rebuilds evidence references from stored selectors, and `reject` records no replacement diagnosis.

## Persistence and recovery

Local runs default to `AFC_DB_PATH=.data/afc.db`. Compose sets `AFC_DB_PATH=/data/afc.db` and mounts the named `afc_data` volume at `/data`:

```bash
docker compose up --build --detach --wait api
docker compose restart api
docker compose down
```

`docker compose down` preserves the named volume; add `--volumes` only when you intend to delete the review database. The image continues to run as UID/GID `10001:10001`, and `/data` is owned by that runtime user.

SQLite is authoritative. LangGraph coordinates one bounded invocation but is not the durable recovery record. A process crash after a provider request starts can require `resume` after the persisted lease expires. External model work is therefore at-least-once and may be billed more than once, while CAS and immutable IDs prevent duplicated revisions, verifier runs, events, and human decisions.

`afc-review resume` first performs a safe case lookup when no live flag is present. It resumes offline work normally, but refuses to POST a hybrid verification resume or a DeepSeek revision resume without `--allow-live-api`. Direct API callers use `{"allow_live_api": false}` for offline resume and must send `{"allow_live_api": true}` when the recoverable next step can call DeepSeek; omission is treated as false.

## Controlled semantic verification

Hybrid verification is a manual, paid experiment and is never run in CI. Configure `DEEPSEEK_API_KEY` only in the local environment and require the explicit live flag:

```bash
uv run afc-evaluate-review \
  --verifier hybrid \
  --allow-live-api \
  --candidate-id CANDIDATE_ID \
  --output evals/reports/generated/review-semantic-smoke.json
```

The `afc-review create` command likewise refuses `deepseek` diagnosis or `hybrid` verification unless `--allow-live-api` is present, and the same flag is required for paid-capable resume. Generated reports are ignored; never paste or commit a key or raw provider response.

## Security and data boundary

Review SQLite stores a canonical, allowlisted `DiagnosticTraceView`, diagnosis revisions, verifier summaries, events, and human decisions. It does not store raw `TraceIR`, prompts, authorization headers, keys, hidden reasoning, or raw provider bodies. `reviewer_label` is caller-supplied audit text, not an authenticated identity; authentication and RBAC are intentionally outside Phase 3.

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
- `docs/superpowers/specs/2026-07-17-phase3-verification-review-workflow-design.md`
- `docs/evaluation/phase2-diagnosis-evaluation.md`
- `docs/evaluation/phase3-verification-review.md`
- `docs/research/agent-project-landscape.md`
