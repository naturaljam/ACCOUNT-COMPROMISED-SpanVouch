# SpanVouch Contract Catalog

Contract v1 freezes only public, cross-module roots. Package version
`spanvouch==0.2.0` and contract versions are independent.

| Schema identifier | Root type | Producer | Consumer | Compatibility rule | Schema | Valid fixture |
| --- | --- | --- | --- | --- | --- | --- |
| `spanvouch.trace/1.0` | `TraceIR` | trace mappers and API ingestion | diagnosis, verification, evaluation | strict v1 reader; new required field or changed semantics requires major version | `schemas/v1/spanvouch.trace-1.0.schema.json` | `tests/contracts/fixtures/v1/trace.valid.json` |
| `spanvouch.diagnostic-context/1.0` | `DiagnosticContext` | `TraceProjector` | diagnosers and verifiers | strict v1 reader; optional ignorable field is a minor-version change | `schemas/v1/spanvouch.diagnostic-context-1.0.schema.json` | `tests/contracts/fixtures/v1/diagnostic-context.valid.json` |
| `spanvouch.diagnosis/1.0` | `DiagnosisReport` | diagnosis engines | verification, review, evaluation | strict v1 reader; taxonomy identifiers remain extensible strings | `schemas/v1/spanvouch.diagnosis-1.0.schema.json` | `tests/contracts/fixtures/v1/diagnosis.valid.json` |
| `spanvouch.verification/1.0` | `VerifierReport` | deterministic or semantic verifiers | review and evaluation | strict v1 reader; verifier/provider labels are extensible identifiers | `schemas/v1/spanvouch.verification-1.0.schema.json` | `tests/contracts/fixtures/v1/verification.valid.json` |
| `spanvouch.review/1.0` | `DiagnosisReviewDetail` | review application service | API, CLI, evaluation consumers | strict v1 reader; decision/revision invariants require a major change | `schemas/v1/spanvouch.review-1.0.schema.json` | `tests/contracts/fixtures/v1/review.valid.json` |
| `spanvouch.artifact-manifest/1.0` | `ArtifactManifest` | dataset and evaluation provenance collectors | reproducibility tooling and release review | strict v1 reader; provenance omissions or changed hash meaning require a major change | `schemas/v1/spanvouch.artifact-manifest-1.0.schema.json` | `tests/contracts/fixtures/v1/artifact-manifest.valid.json` |

## Common serialization and compatibility rules

- Canonical bytes are UTF-8, compact JSON with sorted keys. Timestamps are UTC
  ISO-8601 values ending in `Z`; hashes are lowercase SHA-256.
- Readers reject unknown fields, unknown schema names or versions, malformed
  payloads, invalid invariants, and canonical-hash mismatches with typed errors.
  They do not silently coerce or guess.
- Backward-compatible optional fields with an ignorable default are a minor
  version change. Removed or renamed fields, changed types/default semantics,
  enum meaning, or required invariants require a major version change.
- Schema migrations are explicit, pure, auditable transformations. Package
  releases do not by themselves change a contract version.

## Deliberately non-contract internals

The following remain implementation details: SQLite schema/rows/SQL/CAS/leases;
LangGraph state, nodes, reducers and commands; `ReviewRuntimeBundle`; FastAPI
dependency wiring and internal DTOs; DeepSeek SDK request/response objects;
SupportLab scenario and mutation-generator state; evaluator accumulators; and
private helpers or exception classes.
