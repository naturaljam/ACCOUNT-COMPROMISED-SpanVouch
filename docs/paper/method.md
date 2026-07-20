# Method

## IVAD and the isolated evidence boundary

Independent Verification of Agent Diagnosis (IVAD) separates diagnosis generation
from verification. A verifier receives only the frozen diagnosis and the
contract-allowed evidence view; it receives no gold label, split identity, expected
finding, competing condition output, credential, provider envelope, or hidden
reasoning.

## Two-stage evaluation

The two-stage design executes matched scenarios through LangGraph and AutoGen only
in Stage A, then freezes content-addressed traces and one diagnosis per eligible
cell. Stage B replays those frozen bytes through B0-B5 without re-executing an agent:
B0 accepts contract-valid diagnoses, B1 applies deterministic verification, B2 uses
shared-context DeepSeek critique, B3 uses isolated DeepSeek verification, B4 uses an
isolated Qwen/vLLM verifier, and B5 composes deterministic and Qwen verification.

Labels remain sealed until all provider work finishes. The post-call evaluator joins
labels and produces manifest-bound observations; only those observations enter the
offline statistical and asset pipeline.
