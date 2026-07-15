import json
from pathlib import Path

import pytest

from afc.evals.generate_dataset import generate_dataset


@pytest.mark.asyncio
async def test_dataset_generation_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_manifest = await generate_dataset(first, seed=7)
    second_manifest = await generate_dataset(second, seed=7)

    assert first_manifest == second_manifest
    assert (first / "traces.jsonl").read_bytes() == (second / "traces.jsonl").read_bytes()
    assert (first / "labels.jsonl").read_bytes() == (second / "labels.jsonl").read_bytes()
    labels = [json.loads(line) for line in (first / "labels.jsonl").read_text().splitlines()]
    assert len(labels) == 20
