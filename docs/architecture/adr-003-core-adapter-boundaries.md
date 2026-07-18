# ADR-003: Keep the Research Core Independent of Adapters

## Status

Accepted on 2026-07-19.

## Decision

The dependency direction is:

```text
contracts <- trace <- diagnosis <- verification <- review
```

`contracts` has no FastAPI, SQLite, LangGraph, provider-SDK, SupportLab, or
evaluation dependency. `trace` depends only on contracts and utility code;
`diagnosis` consumes trace/contracts through ports; `verification` consumes
contracts, trace, and diagnosis but not the review workflow; and `review` uses
the preceding public modules through ports.

DeepSeek is a model-provider adapter, LangGraph is a workflow-runner adapter,
and SQLite is a review-repository adapter. API and CLI are outer delivery
layers that invoke application services rather than constructing adapter
internals. SupportLab and evaluation are consumers at the outer edge: production
core modules must not import them.

## Consequences

- Alternative providers, workflow frameworks, and storage backends can be
  tested against stable ports without changing core semantics.
- SQLite rows, LangGraph state/commands, and provider SDK objects remain
  replaceable internals rather than frozen research contracts.
- Architecture tests enforce the direction and prohibit reverse imports.
