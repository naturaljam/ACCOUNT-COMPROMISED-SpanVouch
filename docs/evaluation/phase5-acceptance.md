# Phase 5 Acceptance Status

## Decision

Phase 5 has completed its offline engineering acceptance gates. It is **not yet
accepted as research evidence**. Fake or deterministic provider output cannot
substitute for a formal experiment.

Formal DeepSeek/Qwen evidence: not collected. Cloud GPU: unapproved.

## Acceptance checklist

| Gate | Status | Evidence or blocker |
| --- | --- | --- |
| Production core and Stage A/Stage B import boundaries | complete | `tests/architecture/test_phase5_boundaries.py` |
| Provider-visible schema and checked-in artifact secret gates | complete | offline architecture test |
| Offline-only Phase 5 CI command | complete | `.github/workflows/ci.yml` |
| SupportLab/OpsLab through LangGraph and AutoGen | complete | deterministic zero-provider E2E covers both labs and both adapters |
| Deterministic B0-B5 offline reference bundle | complete | byte-reproduced fake-provider bundle generated from clean commit `73b40480609325f8e80d41629573a6bab754ade0`; manifest file SHA-256 `17899e14a53f685498b37707fae25263ff6a76cebc80c2060ee86c9de13550bb` |
| Offline full suite and coverage | complete | 1,610 passed, 1 skipped; 93.58% coverage on the final integrated branch |
| Reproducible wheel and Docker acceptance | complete | repeated wheel hash; non-root, health, writable persistence, restart, teardown, and no-residue checks passed |
| Offline CI gates | complete | architecture, security, coverage, wheel, and Docker gates are configured without provider credentials |
| Paid pilot approval and evidence | blocked by explicit approval | no approved spend or provider run |
| Frozen formal experiment approval | pending pilot review | no formal manifest approved |
| Complete paired DeepSeek/Qwen formal matrix | not collected | no paper-effectiveness result |
| H1-H5 claim-gate outcomes | unresolved | requires verified formal analysis manifest |

## Non-claims

- Framework portability is not framework equivalence.
- Verifier disagreement is not evidence that either verifier is correct.
- OpsLab results cannot establish broad domain generalization.
- Missing, undefined, incomplete, or operationally failed cells cannot become a
  supported claim.
- A green offline CI run establishes engineering isolation and reproducibility,
  not an improvement in diagnosis reliability.

## Required handoff before research acceptance

The remaining acceptance owner must attach exact commits and hashes for the
config, corpus, candidates, matrix, evaluated results, and analysis bundle; all
cell counts and missingness; B0-B5 risk/coverage intervals; H1-H5 outcomes;
provider and GPU provenance; actual cost; security/static/build/Docker evidence;
and every null or negative result. The claim ledger may advance only when it
matches the verified claim-gate JSON exactly.

## Offline engineering evidence snapshot

The committed reference bundle is deterministic fake-provider evidence. Its
`manifest.json` file has SHA-256
`17899e14a53f685498b37707fae25263ff6a76cebc80c2060ee86c9de13550bb`.
The generator discovered clean code-under-test commit
`73b40480609325f8e80d41629573a6bab754ade0` from Git; the manifest and
`environment.txt` record that same identity with `dirty_worktree=false`.
The final integrated full suite reported 1,610 passing tests, 1 skipped test, and
93.58% coverage.

The wheel and Docker checks passed. The container ran as UID/GID `10001:10001`,
preserved writable `/data` state across restart, returned healthy, and left no
container, volume, or network residue after teardown. These results establish
offline delivery properties only.
