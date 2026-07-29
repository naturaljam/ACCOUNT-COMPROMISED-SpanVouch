# IVAD preprint

This directory publishes the paper **IVAD: Evidence-Constrained and Risk-Controlled Failure Diagnosis for AI Agents** with its reproducible LaTeX source.

- [Read the PDF](IVAD.pdf)
- [Browse the LaTeX source](source/)

## Build the paper

Run the following commands from `paper/source/` with a TeX distribution that includes `acmart`, TikZ, BibTeX, and their dependencies:

```bash
pdflatex -interaction=nonstopmode -halt-on-error main-arxiv.tex
bibtex main-arxiv
pdflatex -interaction=nonstopmode -halt-on-error main-arxiv.tex
pdflatex -interaction=nonstopmode -halt-on-error main-arxiv.tex
```

The generated file is `main-arxiv.pdf`.

## Evidence scope

The reported results validate deterministic contract behavior, system integrity, recovery, and offline pipeline reproducibility. The paper does not report semantic-verifier effectiveness, real-provider gains, or empirical target-risk attainment.

## Copyright

Copyright 2026 Hanzhe Liu. The SpanVouch software is licensed under the repository's MIT License. The paper, figures, and paper source are not covered by that software license. Redistribution from this repository is permitted; other reuse requires the copyright holder's permission unless a separate paper license is granted.
