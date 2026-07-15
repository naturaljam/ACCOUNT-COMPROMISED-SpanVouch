# ADR-001: Use TraceIR as the Stable Diagnosis Boundary

## Status

Accepted on 2026-07-15.

## Context

Agent frameworks and observability backends expose different trace shapes. Diagnosis, regression generation, and evaluation must not depend directly on a LangGraph, Phoenix, or provider-specific object.

## Decision

AFC accepts OpenTelemetry/OpenInference-style spans through an adapter and converts them into immutable TraceIR v1 objects. Domain and evaluation modules depend only on TraceIR. The first adapter maps in-process OpenTelemetry `ReadableSpan` values; future adapters must preserve TraceIR invariants and pass the same contract tests.

## Consequences

- Positive: diagnosis code is portable and deterministic tests do not require Phoenix.
- Positive: source-specific secrets and unsupported attributes can be removed at the adapter boundary.
- Negative: the adapter owns schema evolution and must reject unsupported or malformed span graphs.
- Constraint: TraceIR v1 changes require a schema version change or backward-compatible optional fields.
