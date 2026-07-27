# SpanVouch Open-Source Release Design

**Date:** 2026-07-27
**Status:** Approved for implementation

## Objective

Publish SpanVouch as an engineering project for diagnosing and reviewing failures in
tool-using agents. The public presentation emphasizes the operational loop already present
in the verified codebase: trace ingestion, evidence-backed diagnosis, independent
verification, bounded revision, human decision, durable recovery, regression evaluation,
and reproducible offline checks.

IVAD remains the name of the evidence-verification protocol. SpanVouch is not presented
primarily as a paper, and the release makes no claim based on uncollected experiments.

## Release Source

The release starts from verified commit `434a615` on the completed Phase 5 engineering
line and preserves all history leading to it. The later Phase 5 budget-freeze commit is
excluded because fresh verification found stale identity-bound fixtures. Phase 6 commits
and uncommitted Phase 6 work are also excluded from this release and remain available for
future development.

The existing GitHub repository will be renamed to `SpanVouch` and made public only after
the release commit passes all publication gates. No history rewrite or force-push is part
of this release.

## Public README

The README will lead with the engineering problem and working system, then explain:

1. the end-to-end workflow and trust boundaries;
2. concrete capabilities and explicit non-goals;
3. an offline quick start that needs no provider key;
4. API and CLI paths for diagnosis, verification, review, and recovery;
5. architecture and repository layout;
6. reproducibility, testing, security, and optional provider guidance;
7. project status based only on verified repository evidence;
8. license, contribution, and security-reporting entry points.

Research documents remain as technical background but do not drive the README narrative.
The README does not advertise Phase 6 as complete or imply that offline engineering
evidence demonstrates improved diagnosis accuracy.

## Release Assets

The release retains the MIT license, container configuration, frozen datasets, evaluation
artifacts, architecture decisions, and CI workflow. It adds concise contribution and
security guidance for public collaboration. No feature, protocol behavior, paid-provider
run, GPU experiment, or Phase 7 work is added merely for presentation.

## Verification Gates

Before the public push:

1. run the complete test suite, Ruff, strict mypy, wheel build, and Compose validation;
2. verify README commands in the isolated release worktree;
3. inspect tracked release content for credentials, local absolute paths, generated caches,
   and private operational data;
4. review the final diff against this design and the existing engineering contracts;
5. report all known limitations without converting them into research claims.

Any failed gate blocks publication or is disclosed accurately.

## GitHub Publication

After all gates pass, commit the release documentation, push the release branch without
force, make it the default public line, rename the repository to `SpanVouch`, update its
description and topics, and change visibility to public. Confirm the final URL, visibility,
and default-branch commit after publication.

If GitHub authentication lacks a required permission, preserve the verified release commit
and report the exact remaining operation.
