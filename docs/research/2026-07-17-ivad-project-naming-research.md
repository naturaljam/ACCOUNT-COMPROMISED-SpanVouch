# IVAD project naming research: from AFC to a public open-source brand

> Research date: 2026-07-17
> Scope: English name for the open-source engineering project. This is a naming and basic-availability screen, **not** trademark, company-name, package-security, or legal clearance.

## Recommendation

Adopt **SpanVouch** as the project and GitHub brand.

> **SpanVouch — Evidence-backed diagnosis for AI agent traces.**

The name is short, pronounceable ("span-vouch"), uses the OpenTelemetry-native unit that the system actually verifies, and states the differentiator without promising impossibility-level certainty: the system *vouches for a diagnostic claim only when its trace evidence supports it*. It works in a paper, README, CV, CLI, and package namespace:

```text
SpanVouch                 # public repository and product name
IVAD                      # paper method: Independently Verified Agent Diagnosis
spanvouch / spanvouch-core # later Python distribution/module candidates
```

At the time of this preliminary screen, exact `SpanVouch` produced no PyPI project, no GitHub repository-name hit, and no registered `.com` record returned by Verisign RDAP. See [availability screen](#basic-availability-screen) for the important limits of that statement.

## What successful adjacent OSS names teach us

The comparison intentionally uses only first-party documentation, official project sites, official GitHub organizations, and official package/registry endpoints.

| Project | Official positioning | Naming pattern | Practical lesson for this project |
|---|---|---|---|
| [LangChain](https://github.com/langchain-ai/langchain) | An agent engineering platform built from interoperable components and integrations. | `Lang` + familiar compositional primitive. | A family name can make related projects legible, but we should not use `Lang*`: it would suggest an affiliation with LangChain. |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Low-level orchestration for long-running, stateful agents. | Same family stem + precise technical abstraction. | A technical noun can be strong when it names the system's unit of work. For IVAD that unit is a trace/span, not a generic “agent”. |
| [Langfuse](https://langfuse.com/) | Open-source platform to trace, evaluate, and improve LLM applications. | Short coined compound; “fuse” suggests joining signals. | A concise coined compound can cover a broad product lifecycle without keyword stuffing. Avoid `-fuse` to preserve distinction. |
| [Arize Phoenix](https://github.com/Arize-ai/phoenix) | Open-source AI observability for tracing, evaluation, experiments, and troubleshooting. | Singular, evocative metaphor. | Metaphor is memorable, but it gives little immediate search meaning and is harder to clear; use it only when the metaphor is uniquely ownable. |
| [OpenTelemetry](https://opentelemetry.io/docs/what-is-opentelemetry/) | Vendor-neutral framework/toolkit for generating, collecting, and exporting traces, metrics, and logs; explicitly not an observability backend. | Descriptive category name. | The standard vocabulary (`trace`, `span`, `telemetry`) gives interoperability credibility. `SpanVouch` deliberately borrows the precise unit, not the full generic category. |
| [DeepEval](https://github.com/confident-ai/deepeval) | Open-source evaluation framework for LLM systems, positioned as pytest-like for LLM apps. | Compressed category phrase. | Clear and searchable, but `*Eval` is now crowded and would underspecify independent verification and risk control. |
| [TruLens](https://github.com/truera/trulens) | Evaluation and tracking for LLM experiments/AI agents, with feedback functions and OTel tracing. | Coined truth + visibility metaphor. | A compact “trust/visibility” word can communicate intent. Avoid `Tru*` lookalikes that would be too close to this adjacent project. |
| [AgentOps](https://github.com/agentops-ai/agentops) | Platform/SDK for building, evaluating, and monitoring AI agents. | Direct category label. | Immediate comprehension, but `*Ops` has become a broad product category and is too generic for a research method. |
| [Pydantic](https://github.com/pydantic/pydantic) | Data validation using Python type hints. | Distinctive coined technical brand. | A non-literal name can age well if the tagline is literal. This supports a compact brand plus a concrete one-line promise. |

## Naming styles worth considering

| Style | Examples above | Benefit | Risk | Fit for this project |
|---|---|---|---|---|
| Ecosystem family + technical noun | LangChain, LangGraph | Clear relationship between products; intuitive to developers. | Can look affiliated with an existing ecosystem or make future scope feel locked in. | Do not use `Lang*`; a standalone technical noun such as `Span*` is better. |
| Short coined compound | Langfuse, Pydantic | Memorable, reasonably ownable, works for GitHub and papers. | Needs a tagline for first-read clarity. | **Best fit.** Combine a trace/evidence unit with a trust action. |
| Evocative metaphor | Phoenix | Strong personality and visual identity. | Often crowded, opaque in search, and easily overclaims. | Secondary option only; the research contribution benefits from immediate precision. |
| Compressed functional category | DeepEval, AgentOps, OpenTelemetry | Obvious purpose and strong discoverability. | Generic, crowded, and risks being mistaken for a broad observability/eval platform. | Useful as a subtitle, not the main brand. |
| Trust/visibility coined word | TruLens | Signals reliability without a long compound. | Close semantic neighbors create confusion; hard to make unique. | Use the concept (`vouch`) rather than a `Tru*` lookalike. |

## Candidate set

The names below are creative candidates, not availability claims unless explicitly included in the screen. “Good” means easy to say in English, suitable for a GitHub repository, and broad enough for the system plus the paper artifact.

| Candidate | Intended meaning | Notes |
|---|---|---|
| **SpanVouch** | A verifier vouches only for trace-span-grounded diagnoses. | **Recommended.** It captures evidence, selective acceptance, and OpenTelemetry compatibility. |
| **TraceVouch** | Trace-level counterpart: evidence-backed diagnostic verdicts. | Strong alternative if the public surface is report/trace oriented rather than span oriented. |
| **VouchSpan** | A compact inversion of SpanVouch. | Technically crisp; slightly less natural when spoken. |
| **SpanSift** | Sifts a trace to find supported fault evidence. | Excellent for triage/diagnosis; weaker on the “independent verification” promise. |
| **EvidenceSpan** | Ties every diagnosis to specific evidence spans. | Most explicit; less distinctive and longer. |
| **Prooflane** | A bounded, auditable route from claim to supporting evidence. | Friendly and memorable; “proof” may overstate empirical/conformal claims. |
| **Tracewright** | A craftsperson for understandable, trustworthy execution traces. | Distinctive and warm; does not reveal verification on first read. |
| **Veridact** | `veridical` + `act`: correct, accountable agent actions. | Strong paper-style coined word; pronunciation needs one exposure. |
| **Corrova** | From corroboration: independent evidence strengthens a claim. | Elegant brand-like coinage; less directly searchable. |
| **Evidara** | Evidence plus a calm, product-like ending. | Broad enough for the full platform; needs a literal subtitle. |
| **TraceBound** | Diagnostics are bounded by actual trace evidence and risk rules. | Clearly matches the method; sounds slightly formal. |
| **TracePact** | A contract between diagnostic claims and trace evidence. | Directly reflects the Claim–Evidence Contract. |
| **ProofMesh** | Multiple verification signals form a mesh of support/counter-evidence. | Suits dual channels; can be confused with security/networking products. |
| **SpanWise** | Span-level evidence used with judgment. | Short and friendly; generic adjective construction. |
| **Veridome** | A space/domain for veridical (truth-grounded) agent analysis. | Academic tone; meaning is less immediate. |
| **ClaimForge** | Turns raw traces into structured, auditable claims. | Good developer energy; emphasizes generation more than conservative acceptance. |
| **CauseMark** | Marks the defensible causal step/entity in a failure trace. | Good for root-cause analysis; slightly narrow for broader evidence verification. |
| **FaultWise** | Makes failure diagnosis careful rather than confident-by-default. | Easy to understand; generic and not clearly agent-specific. |
| **AuditLoom** | Weaves traces, evidence, and decisions into an audit trail. | Evocative and distinctive; less direct on diagnosis. |
| **ProofWork** | The engineering work required to substantiate an agent diagnosis. | Simple, but has a broader non-AI meaning. |
| **RootRail** | A guarded route from observed failure to causal root. | Memorable, yet could be mistaken for infrastructure tooling. |
| **GroundFold** | Folds evidence grounding and risk control into one workflow. | Distinctive, but abstract. |
| **CausaLens** | A lens for causal diagnosis under evidence constraints. | Research-friendly, but has a Latinate pronunciation burden. |
| **TraceHaven** | A safe place to inspect and audit agent trajectories. | Product-friendly, but too soft for the core research claim. |

## Basic availability screen

### Method and limitation

Checked on 2026-07-17:

1. Exact Python project endpoint on [PyPI](https://pypi.org/).
2. Repository-name substring search through the [official GitHub REST Search API](https://docs.github.com/en/rest/search/search#search-repositories).
3. `.com` record response from [Verisign's official RDAP service](https://rdap.verisign.com/com/v1/domain/).

A `404` means the queried service did not return an exact record at that moment. A GitHub zero is a repository-search result, not proof that no account, code symbol, organization, trademark, package on another registry, or similar spelling exists. RDAP does not cover other top-level domains or legal rights. **This is only an early elimination screen; obtain a formal trademark/company/domain review before public release or incorporation.**

| Candidate | PyPI exact endpoint | GitHub repo-name search | `.com` RDAP | Decision |
|---|---:|---:|---:|---|
| **SpanVouch** | 404 | 0 | 404 | **Best preliminary signal.** |
| **VouchSpan** | 404 | 0 | 404 | Clean preliminary signal. |
| **EvidenceSpan** | 404 | 0 | 404 | Clean, but more descriptive/generic. |
| **SpanSift** | 404 | 0 | 404 | Clean preliminary signal. |
| **TraceVouch** | 404 | 0 | 404 | Clean preliminary signal. |
| TraceSieve | 404 | 1 | 404 | Avoid: an existing research-project repository hit. |
| TraceVerdict | 404 | 1 | 200 | Avoid: direct adjacent agent-evaluation collision and registered domain. |
| ProofSpan | 404 | 0 | 200 | Second tier only: registered domain. |
| AgentVerity | 404 | 0 | 200 | Second tier only: registered domain. |
| RunVerdict | 404 | 4 | 200 | Avoid: existing repository namespace and registered domain. |

Useful reproducible query forms (replace `NAME`): [PyPI JSON](https://pypi.org/pypi/spanvouch/json), [GitHub repository search](https://api.github.com/search/repositories?q=SpanVouch+in%3Aname), and [Verisign RDAP](https://rdap.verisign.com/com/v1/domain/spanvouch.com). The exact current status must be rechecked immediately before reserving any name.

## AFC and IVAD: keep the method, rename the product

### AFC

Do **not** use AFC as the public product name. It is short but has no obvious meaning to a new reader and is highly ambiguous across domains. It should remain only as a legacy internal/codename reference in historical documents, migrations, and previous experiment provenance. New README, package, paper title, and public repository language should stop leading with it.

### IVAD

Keep **IVAD** as the method/paper acronym: **Independently Verified Agent Diagnosis**. It accurately names the scientific contribution—claim–evidence contracts, separated verification channels, calibrated selective acceptance, and bounded evidence acquisition. It is less suitable as the public product brand because it is hard to parse and pronounce without expansion, and it carries no trace/evidence cue by itself.

The deliberate pairing is therefore:

```text
SpanVouch: the open-source system and artifact
IVAD: the independently verified agent diagnosis method implemented/evaluated by SpanVouch
```

This keeps paper terminology precise while giving the engineering project a memorable identity. It also prevents a common open-source problem: continuously renaming a product whenever the research method evolves.

## Decision

Use **SpanVouch** as the working public name, with **“Evidence-backed diagnosis for AI agent traces”** as the one-line descriptor. Reserve **TraceVouch** and **SpanSift** as fallbacks until a pre-release legal/brand screen is complete. Retain **IVAD** only for the method and paper; retire **AFC** from new public-facing names.
