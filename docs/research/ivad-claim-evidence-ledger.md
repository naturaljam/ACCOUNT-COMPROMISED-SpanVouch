# IVAD Claim–Evidence Ledger

This ledger separates engineering regression evidence from paper-effectiveness
evidence. Phase 4 establishes reproducible research infrastructure; it does not
establish a paper effectiveness result.

| Proposed claim | Status at Phase 4 | Evidence required before a paper claim |
| --- | --- | --- |
| Deterministic verification detects the defined structural, reference, and scope defects | engineering regression only | expanded mutation benchmark with confidence intervals |
| Semantic verification improves error-diagnosis detection | needs evidence | isolated experiment, human/executable ground truth, and strong baselines |
| Independent verifier isolation reduces correlated failure | needs evidence | same-model, cross-model, cross-provider, and shared-context ablations |
| Risk control attains target alpha | needs evidence | frozen calibration protocol and independent ID test |
| One-time evidence acquisition restores coverage without violating risk | needs evidence | post-acquisition calibration and cost/coverage curves |
| Conclusions generalize across domains and frameworks | needs evidence | Phase 5 framework labs and Phase 9 OOD evaluation |
| Phase 5 verification conditions improve diagnosis quality across frameworks | planned; no Phase 5 evidence yet | frozen Phase 5 protocol, paired clustered analysis, and formal results |
| Phase 5 verifier isolation reduces correlated failure | planned; no Phase 5 evidence yet | frozen isolation ablation, formal results, and cross-model comparison |
| Phase 5 risk control meets the preregistered analysis policy | planned; no Phase 5 evidence yet | frozen formal configuration, paired bootstrap intervals, and Holm-corrected results |

The frozen 36-candidate deterministic results are a regression guard for known
engineering behavior, not statistical evidence of general effectiveness.

Phase 5 architecture and CI checks are offline engineering acceptance evidence
only; they are not paper evidence. Formal DeepSeek/Qwen evidence: not collected.
Cloud GPU: unapproved. Until a complete frozen formal matrix produces a verified
analysis manifest and claim-gate JSON, the Phase 5 rows above remain unchanged.

## Phase 5 claim-gate discipline

The three Phase 5 rows above remain `planned; no Phase 5 evidence yet` until a
verified Phase 5 analysis manifest (`spanvouch.phase5-analysis-manifest`) exists.
At that point, and only at that point, each H1-H5 statement must copy its
`supported`, `contradicted`, or `unresolved` outcome from `claim-gates.json` and
cite the exact IDs and SHA-256 values for `metrics-by-condition.csv`, `paired-effects.csv`,
`failure-accounting.csv`, and `risk-coverage.csv`. Engineering fixtures and fake
provider bundles never advance a paper claim.
