# Phase 5 Offline Smoke Reference

This is deterministic fake-provider engineering evidence and not paper evidence.
It performs no live provider request and no GPU operation.

Reproduce twice and compare the two `bundle` directories byte-for-byte:

`uv run --python 3.12.7 python -m spanvouch.evaluation.offline_acceptance --output-dir phase5-offline-smoke`
