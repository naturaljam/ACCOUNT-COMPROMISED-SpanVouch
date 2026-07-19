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
