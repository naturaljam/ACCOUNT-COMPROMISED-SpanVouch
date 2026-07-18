# ADR-002: Version Public Contracts Independently of the Package

## Status

Accepted on 2026-07-19.

## Context

SpanVouch has six public roots that cross modules, processes, and research
artifacts. A package release can change implementation details without changing
their serialized meaning. Conversely, a public payload may need a deliberate
compatibility change independent of the package release cadence.

## Decision

Each root carries its exact `schema_name` and `schema_version`. Contract v1 uses
the six identifiers listed in the contract catalog. Canonical serialization is
one implementation: UTF-8, sorted-key compact JSON, UTC `Z` timestamps,
lowercase SHA-256, and rejected NaN/Infinity. Strict readers reject unknown
fields, unsupported versions, malformed values, invariant violations, and hash
mismatches with typed failures.

An additive optional field that old readers can ignore is a minor version.
Removing or renaming a field, changing type/default semantics, changing enum
meaning, or changing an invariant is a major version. Migrations are explicit,
pure, tested transformations; readers never best-effort coerce an unknown
payload. `spanvouch==0.2.0` is not a contract-version promise.

## Consequences

- Fixtures and schemas are release evidence and are not overwritten in place.
- New contracts can evolve without coupling every implementation-only package
  change to a schema bump.
- Consumers receive clear compatibility errors instead of accepting ambiguous
  research evidence.
