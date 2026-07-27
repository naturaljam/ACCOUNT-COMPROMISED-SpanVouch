# SpanVouch Open-Source Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the already verified SpanVouch engineering loop with accurate public documentation and a reproducible GitHub release.

**Architecture:** Work from commit `434a615` on a dedicated release branch. Change only public documentation and repository metadata, then run the existing offline delivery gates before pushing without history rewriting.

**Tech Stack:** Python 3.12, uv 0.8.x, FastAPI, Pydantic, SQLite, LangGraph, AutoGen, pytest, Ruff, mypy, Docker Compose, GitHub CLI.

## Global Constraints

- Present SpanVouch as an engineering system; IVAD is the protocol name, not the primary product pitch.
- Do not claim Phase 6 completion or publish uncommitted Phase 6 work.
- Do not perform paid provider calls, GPU experiments, or Phase 7 work.
- Preserve Git history and never force-push.
- Publish only after fresh local verification and a credential scan.

---

### Task 1: Public Project Documentation

**Files:**
- Modify: `README.md`
- Create: `CONTRIBUTING.md`
- Create: `SECURITY.md`

**Interfaces:**
- Consumes: existing CLI commands, API routes, architecture documents, CI gates, and MIT license.
- Produces: the public onboarding, contribution, and vulnerability-reporting contract.

- [ ] **Step 1: Replace the phase-led README narrative**

Write a README whose first sections are `Why SpanVouch`, `Engineering loop`,
`Capabilities`, and `Quick start`. Describe only behavior available at commit `434a615`.
Keep IVAD as a concise protocol note and move research links to a final background section.

- [ ] **Step 2: Add verified runnable paths**

Include these exact offline commands:

```powershell
uv sync --frozen --group dev
uv run spanvouch dataset generate --output .cache/readme-check --seed 20260715
uv run spanvouch evaluate diagnosis --output .cache/rules.json
uv run spanvouch evaluate review --output .cache/review-rules.json
uv run uvicorn spanvouch.api.app:app --host 127.0.0.1 --port 8000
```

Document `POST /v1/traces`, `POST /v1/traces/{trace_id}/diagnosis-reviews`,
`GET /v1/diagnosis-reviews/{case_id}`, resume, and decision endpoints. State that provider
paths require explicit live authorization and are not needed for the offline quick start.

- [ ] **Step 3: Add public collaboration guidance**

Create `CONTRIBUTING.md` with environment setup, focused/full verification commands,
contract and generated-artifact rules, and a pull-request checklist. Create `SECURITY.md`
that asks reporters to use GitHub private vulnerability reporting and explicitly prohibits
placing credentials, raw provider payloads, or sensitive traces in public issues.

- [ ] **Step 4: Validate documentation references**

Run:

```powershell
rg -n "Phase 6.*complete|paper project|research project|production-ready" README.md CONTRIBUTING.md SECURITY.md
rg -o '`[^`]+`' README.md CONTRIBUTING.md SECURITY.md
git diff --check
```

Expected: no unsupported completion or readiness claim, all referenced repository paths
exist, and `git diff --check` exits 0.

- [ ] **Step 5: Commit public documentation**

```powershell
git add README.md CONTRIBUTING.md SECURITY.md docs/superpowers/specs/2026-07-27-spanvouch-open-source-release-design.md docs/superpowers/plans/2026-07-27-spanvouch-open-source-release.md
git commit -m "docs: prepare SpanVouch for open source"
```

### Task 2: Release Verification

**Files:**
- Verify: `src/`, `tests/`, `.github/workflows/ci.yml`, `Dockerfile`, `compose.yaml`

**Interfaces:**
- Consumes: the committed release candidate.
- Produces: fresh local evidence for tests, static checks, buildability, container config,
  and public-content hygiene.

- [ ] **Step 1: Run static and package checks**

```powershell
uv run --no-sync ruff check src tests
uv run --no-sync mypy
uv build --wheel --build-constraints build-constraints.txt --require-hashes --no-cache
docker compose config --quiet
```

Expected: every command exits 0 and a wheel exists under `dist/`.

- [ ] **Step 2: Run the complete offline suite**

```powershell
uv run --no-sync pytest --cov=spanvouch --cov-report=term-missing --cov-fail-under=93
```

Expected: all runnable tests pass, the intentional environment-dependent test remains
skipped, and total coverage is at least 93%.

- [ ] **Step 3: Exercise README commands**

Run the four non-server quick-start commands exactly as documented. Start the API locally,
request `GET /health`, verify `{"status":"ok","service":"spanvouch"}`, and stop the
process without leaving a listener.

- [ ] **Step 4: Scan release content**

```powershell
git status --short
git diff HEAD^ --check
git grep -n -I -E "(sk-[A-Za-z0-9_-]{16,}|api[_-]?key[[:space:]]*[:=][[:space:]]*[^$<{[:space:]]+)"
git ls-files | rg "(^|/)(\.env$|\.venv/|\.pytest_cache/|\.mypy_cache/|\.ruff_cache/|tmp/)"
```

Expected: only intended release files are changed, no credential value is found, and no
cache or local temporary path is tracked.

### Task 3: GitHub Publication

**Files:**
- Modify remotely: repository name, description, topics, visibility, and default branch.

**Interfaces:**
- Consumes: verified `codex/open-source-release` commit and authenticated GitHub CLI.
- Produces: public `SpanVouch` repository with the verified release as its default line.

- [ ] **Step 1: Verify GitHub authority and current repository state**

```powershell
gh auth status
gh repo view naturaljam/Agent_Failure_Clinic --json nameWithOwner,visibility,defaultBranchRef,url
```

Expected: authenticated account has repository administration and push access.

- [ ] **Step 2: Push without force and promote the release**

```powershell
git push -u origin codex/open-source-release
gh pr create --base main --head codex/open-source-release --title "Release SpanVouch" --body "Publishes the verified SpanVouch engineering loop with an engineering-first README, contribution guidance, security policy, and fresh offline verification evidence. No Phase 6 or paid-provider result is included."
```

Merge only after GitHub checks pass.

- [ ] **Step 3: Rename and open the repository**

```powershell
gh repo rename SpanVouch --repo naturaljam/Agent_Failure_Clinic --yes
gh repo edit naturaljam/SpanVouch --description "Evidence-backed diagnosis, verification, and review infrastructure for tool-using agents" --add-topic ai-agents --add-topic observability --add-topic evaluation --add-topic fastapi --visibility public --accept-visibility-change-consequences
```

- [ ] **Step 4: Verify the public result**

```powershell
gh repo view naturaljam/SpanVouch --json nameWithOwner,visibility,defaultBranchRef,url
git ls-remote origin refs/heads/main refs/heads/codex/open-source-release
```

Expected: repository is public, named `naturaljam/SpanVouch`, and its default branch points
to the verified release history.
