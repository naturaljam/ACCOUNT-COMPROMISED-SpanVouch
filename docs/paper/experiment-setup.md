# Experiment Setup

SupportLab is the primary domain and OpsLab is a preliminary replication domain.
Both use matched scenario behavior, injection triggers, seeds, limits, and terminal
predicates across LangGraph and AutoGen. The frozen config hash binds repetitions,
conditions, prompt/config hashes, model identifiers, endpoint classes, generation
settings, exclusions, missingness policy, and the coverage-loss tolerance.

DeepSeek is the sole diagnosis generator. B2 and B3 use the same DeepSeek model,
instruction, schema, sampling settings, and token budget; only provider-visible
context differs. B4 and B5 use the pinned Qwen checkpoint through a pinned vLLM
deployment. This is an operational cross-model comparison, not a pure model-only
intervention.

Inference uses scenario-template clustered paired bootstrap intervals with the
frozen analysis seed and draw count, exact McNemar tests where specified, and Holm
correction. Every result retains numerator, denominator, interval method, seed,
source artifact hash, costs, missingness, and operational failure accounting. Paid
work is constrained by the preregistered monthly budget, pilot fraction, and global
stop rule. Pilot rows and contract-invalid or unpaired cells follow the frozen
exclusions policy and are never silently removed after outcomes are observed.
