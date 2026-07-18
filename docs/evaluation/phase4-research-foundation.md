# Phase 4 Research Foundation Acceptance

## Evidence identity

- Branch: `feature/phase4-research-foundation`.
- Frozen Phase 3 marker: `phase3-frozen-20260718` =
  `0d33812cc8b559bacc8df82fd5c0bcaeb6c03fb7`.
- Clean release-candidate and code-under-test commit:
  `6f78f8205122262261ebf752bbbece79ef864f28`.
- The offline reference bundle was generated from that clean candidate with
  `dirty_worktree=false`; its manifest records the same commit. This report's
  final evidence commit intentionally does not claim its own SHA. Audit the
  report commit with `git log -1` and use the explicit code-under-test SHA above.
- The immutable frozen dataset tree and
  `docs/evaluation/phase3-verification-review.md` have no diff from the marker.

## Contracts

The six v1 roots and valid fixtures are catalogued in
[`docs/contracts/catalog.md`](../contracts/catalog.md). Schema / fixture SHA-256
pairs are:

| Root | Schema | Fixture |
| --- | --- | --- |
| artifact manifest | `21f1a9308d3aad9b7f38d317702882a0966239c36ab458cd060b191d3a372c42` | `d9490de16f41d1045c997914829767809947d77bed2cdd138175e2a4c4672ae5` |
| diagnosis | `4f9dc45578a8044073400cbd8a0ddd877d2e4108ac2c4eb51a3986acd6c6748a` | `dc8ea38816d6dc803a226b73a5692b1a41d74c2a9f02fb7c2ba2e27a7e9d8fe9` |
| diagnostic context | `3a610985424cdbe45a9a3f8288222b125ac7e75164b84b93d05075e6508decb6` | `abed6060b451c7ed1042ca02ac07982b4a192e7e837afdbc9d975d88efc6963a` |
| review | `e585cdc16deba4231bb1ad7edd30a72a09124c2cdec02532dde7652a299ae157` | `e1f841676c751b46a6d61ecc5065c445d36e144221c9bc2f23151f1469c79d9d` |
| trace | `dc62ed892da0495dfcd56282636c667ecd3e16c64a9777b5e7564ca25f0f5e09` | `2eed3637855ec7560bf72c1cd4e993c124af1bd1735fcbaa9dd1d1f738ce0a88` |
| verification | `e2d46a6f59e521945d7d4638106a01b71ade30fde8da802e4c3a92afe4575b9f` | `0ba6a10422b15f41b15dc2425aceb0cc53061a6e94704495aa419c3cefcc8630` |

`tests/contracts tests/architecture -v` passed 104 tests. These tests cover
strict schema/version/field/hash failures, canonical stability, and the core
dependency direction.

## Frozen assets and deterministic evaluation

Frozen Phase 3 file hashes remain:

- SupportLab manifest: `b14eac192e7b683fb908f2f7f54efccb31ab100bf19563476b824d192060cb38`.
- Review manifest: `677e0075f5b4149db73538411376bf994caa5ba0fdb8ff29b33b487a5fe02076`.
- Review candidates: `ee04d8d0f1e608fd81c202fca39eeb799f764b3099cfb03d7d94a4ab7eb73bd2`.
- Review labels: `d41a87247456264863d70f807256a5d1b6f24ab84422dc406a92ef867e36b305`.

Two clean-candidate runs were byte-identical:

- Diagnosis: `ed00b565e7910f143b4844213fd390343af074e3930724934c0c8a9da40b797a`.
- Review: `c3fc1f4fc2015cbc0a3d6691bf01da98e1a77f7ac139d37f7e75cd2038742f9b`.

Review quality rates were valid pass `1.0`, hard-defect recall `1.0`,
unsupported-scope detection `1.0`, and operational errors `0.0`. Diagnosis and
review provider samples, input tokens, output tokens, and total tokens were all
zero.

The committed historical bytes are immutable. Current Phase 4 review generation
intentionally emits contract-root metadata (`schema_name` and nested taxonomy),
and the CLI canonicalizes its generated manifest to bind a bundle. Therefore a
newly generated Phase 4 candidate/manifest is not asserted byte-equal to its
historical AFC-era serialization; semantic generation and frozen on-disk hashes
are separately checked. CI enforces this distinction.

## Release artifact, wheel, and delivery

The clean reference bundle was generated once in `.cache`, secret-scanned, then
moved once to `evals/reports/reference/phase4-offline-bundle`. Its hashes are:

- Manifest: `f8f33da259ff055edf626ac50f35102e1763e7ee00e8d9a046003e945f1ee3a5`.
- Metrics: `c3fc1f4fc2015cbc0a3d6691bf01da98e1a77f7ac139d37f7e75cd2038742f9b`.

The bundle records `provider_status=not_used`, null cost/usage, no models, and
the clean code commit. `tests/review/test_secret_hygiene.py` plus artifact tests
passed 83 tests; only the existing Starlette `TestClient` deprecation warning
appeared. The bundle contains no key, environment value, raw provider response,
prompt text, or hidden reasoning; `prompt_sha256` metadata is allowed and null.

`uv build` produced the `spanvouch-0.2.0` wheel and sdist. Isolated wheel import
printed `spanvouch`; isolated CLI help passed; isolated `import afc` failed with
`ModuleNotFoundError`, as required. The exact active old-name scan across source,
tests, packaging, Docker, Compose, README, and CI returned no hits after negative
tests construct legacy strings at runtime. Repository-wide remaining occurrences
are limited to documented migration text and immutable historical design, handoff,
evaluation, plan, and frozen-provenance records.

## SQLite and Docker

`tests/review/test_sqlite_process_stability.py -q` passed, exercising 20
independent processes. Task 15's reviewed recovery/race suite remains the
supporting recovery evidence: 21 provenance/race tests and two capability probes.

The isolated Compose run built the pinned image, reached `ok/spanvouch`, ran as
UID/GID `10001:10001`, and owned `/data` as `10001:10001`. It created a trace,
created/showed/confirmed a review case, restarted the API, and obtained a
byte-identical terminal review response. The isolated container, network, and
volume were removed. The first script attempt reached a healthy container but
failed before application assertions because PowerShell corrupted a `sh -c`
command substitution; the corrected one-time execution used direct `docker exec`
commands and passed.

## Quality gates and known limitations

`uv sync --frozen --group dev`, Ruff, and strict mypy were clean (84 source
files). The final exact coverage command passed 961 tests with 6401/6848 =
93.4725% coverage. `uv sync` was run with `UV_PYTHON` explicitly set to the checked-in virtual
environment's Python 3.12.7: this host has no managed 3.12.13 interpreter,
although the pinned Docker build validated Python 3.12.13. This is an environment
warning, not a package-constraint change.

The only recurring test warning is Starlette's `TestClient` deprecation for the
installed `httpx`; no new warning was introduced.

POSIX exact-object directory unlink is unsupported. After a rollback claims
the private quarantine directory, the implementation preserves that recovery
evidence and raises the sanitized cleanup conflict; it performs no unlink,
`rmdir`, or `rmtree`. Canonical output and bundle paths are absent, so no
half-published artifact is presented as valid. Windows has the reviewed complete
descendant native-identity and pinned-handle deletion capability. This is a
fail-closed limitation, not a secure POSIX deletion claim.

Phase 4 adds no paper effectiveness result. Deterministic 36-candidate outcomes
are engineering-regression evidence only. Semantic independence, Conformal risk,
evidence acquisition, and OOD generalization remain `needs evidence` in the
claim–evidence ledger.
