# Contributing to SpanVouch

Thank you for improving SpanVouch. Contributions should preserve its evidence boundaries,
offline defaults, and reproducible artifacts.

## Set up development

SpanVouch requires Python 3.12 and uv 0.8.x.

```bash
git clone https://github.com/naturaljam/SpanVouch.git
cd SpanVouch
uv sync --frozen --group dev
```

Create a focused branch, make the smallest coherent change, and include tests for changed
behavior.

## Verify a change

Run focused tests while developing, then run the complete local gate before opening a pull
request:

```bash
uv run ruff check src tests
uv run mypy
uv run pytest --cov=spanvouch --cov-report=term-missing --cov-fail-under=93
uv build --wheel --build-constraints build-constraints.txt --require-hashes --no-cache
docker compose config --quiet
```

Changes to the API image or persistence path should also pass the Docker smoke workflow in
`.github/workflows/ci.yml`.

## Change contracts and artifacts

The roots listed in `docs/contracts/catalog.md` are public, versioned contracts. A new
required field, changed meaning, or removed field requires an explicit compatibility
decision and the corresponding schema, fixture, and contract-test updates.

Frozen datasets and evaluation reports are content-addressed evidence. Do not edit generated
bytes by hand. Use the owning generator, review the manifest and provenance changes, and
commit the complete artifact chain. Never replace offline evidence with output from an
unrecorded provider run.

## Preserve provider and secret safety

- Keep provider execution opt-in and fail closed before credentials or network access.
- Never commit `.env` files, API keys, raw provider payloads, hidden reasoning, or sensitive
  traces.
- Use synthetic or sanitized fixtures in tests and issues.
- Preserve budget, allowlist, provenance, and label-isolation checks when changing live
  execution code.

## Open a pull request

A pull request should explain the problem, the chosen boundary, and the verification
evidence. Before requesting review, confirm that:

- the change is scoped and has regression coverage;
- Ruff, mypy, and the full test suite pass;
- contracts and frozen artifacts remain byte-stable unless intentionally updated;
- documentation and examples match the implemented interface;
- no credential, local path, cache, or generated scratch file is tracked.

By contributing, you agree that your contribution is licensed under the repository's MIT
License.
