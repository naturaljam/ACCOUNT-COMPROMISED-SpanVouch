# SpanVouch v0.6.0

SpanVouch v0.6.0 closes the reproducible release handoff for the completed v0.5
DeepSeek-only formal evaluation.

## Release contents

- Adds the offline `spanvouch release verify` gate for package metadata, citation metadata,
  bilingual README release links, and required distribution files.
- Provides deterministic human-readable and canonical JSON verification reports without
  reading credentials, opening network connections, or mutating the checkout.
- Derives the expected version in CI after the locked install and documents the same local
  command in English, Chinese, and contributor instructions.
- Leaves the v0.5 DeepSeek-only experiment, budget controls, B4/B5 policy boundary, and
  fail-closed H1-H5 claim status unchanged.

This release adds no provider, paid experiment, network-backed lookup, runtime dependency,
IVAD mathematical change, or new scientific claim.
