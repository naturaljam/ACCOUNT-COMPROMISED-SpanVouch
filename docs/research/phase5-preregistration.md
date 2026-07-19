# Phase 5 Preregistration: Multi-framework Isolated Verification

## Scope and freeze

Phase 5 evaluates verification conditions across LangGraph and AutoGen with a
fixed seed (`20260719`), the six condition identifiers in the checked-in pilot
configuration, and paired scenario-template clusters. The pilot uses exactly
three repetitions. Pilot observations select a concrete coverage-loss tolerance
and formal repetition count within `evals/configs/phase5-formal-policy.json`;
they are not included in the formal hypothesis tests.

Before collecting formal outcomes, the selected inputs must be passed to
`freeze_formal_config`. It produces a formal configuration with an UTC freeze
timestamp and a canonical SHA-256 digest of every primary configuration field.
The following primary fields cannot change after that freeze: experiment ID,
seed, conditions, frameworks, endpoint and prompt parameters, budget policy,
repetitions, coverage-loss tolerance, and freeze timestamp. Any change requires
a new preregistration version and a separately identified experiment.

## Research questions and hypotheses

| ID | Research question | Preregistered hypothesis |
| --- | --- | --- |
| RQ1 / H1 | Does verification improve diagnosis quality over no verifier? | H1: at least one verification condition improves the primary diagnosis-quality endpoint relative to B0. |
| RQ2 / H2 | How does deterministic verification compare with semantic verification? | H2: semantic verification conditions improve the primary endpoint over B1. |
| RQ3 / H3 | Does independent verifier isolation reduce correlated failure? | H3: isolated semantic verification improves the primary endpoint over same-model shared verification. |
| RQ4 / H4 | Does a cross-model verifier improve isolation results? | H4: B4 improves the primary endpoint over B3. |
| RQ5 / H5 | Does deterministic screening complement cross-model verification? | H5: B5 improves the primary endpoint over B4. |

## Endpoints and analysis

The primary endpoint is paired scenario-level diagnosis quality: a correct,
evidence-supported diagnosis under the fixed evaluation label protocol. Primary
comparisons use paired bootstrap confidence intervals with 10,000 draws,
clustered by scenario template so variants of a template are resampled together.
The required confidence level is 0.95. The planned family of primary pairwise
comparisons receives Holm correction.

Secondary endpoints are diagnosis coverage, evidence-support quality, false
positive rate, abstention rate, latency, and cost. McNemar tests are a secondary
paired analysis for binary endpoint disagreements; they do not replace the
clustered paired-bootstrap primary analysis.

## Exclusions, missingness, and coverage

Exclusions are limited to pre-specified malformed traces, unrecoverable
execution failures before a condition can run, and duplicate case identifiers.
Every exclusion records its condition, framework, scenario template, and reason.
Provider or harness failures after an attempted run remain visible as missing
outcomes rather than being silently removed. Analyses report missingness by
condition and framework, use paired complete cases for a pairwise endpoint, and
report the corresponding denominator.

The pilot selects a coverage-loss tolerance no greater than 0.10 and records it
in the frozen formal configuration. The formal analysis reports coverage change
against this tolerance alongside the primary endpoint; it does not adjust the
tolerance after formal data are observed.

## Transparency rule

All preregistered comparisons, including null, negative, and inconclusive
results, remain visible in the formal report. Exploratory analyses are labeled
as exploratory and are not substituted for the primary or secondary endpoints.
