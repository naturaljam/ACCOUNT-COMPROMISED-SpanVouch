from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest

from spanvouch.evaluation.artifacts import read_verified_directory_tree
from spanvouch.evaluation.offline_acceptance import run_offline_acceptance

REFERENCE = Path("evals/reports/reference/phase5-offline-smoke")


def _forbid_socket(*args: object, **kwargs: object) -> None:
    del args, kwargs
    raise AssertionError("offline acceptance attempted a socket connection")


async def _forbid_http(*args: object, **kwargs: object) -> None:
    del args, kwargs
    raise AssertionError("offline acceptance attempted an HTTP request")


@pytest.mark.asyncio
async def test_zero_provider_pipeline_is_network_free_and_logically_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket.socket, "connect", _forbid_socket)
    monkeypatch.setattr(httpx.AsyncClient, "send", _forbid_http)

    first = await run_offline_acceptance(tmp_path / "first")
    second = await run_offline_acceptance(tmp_path / "second")

    assert first == second
    assert first.adapter_execution_count == 4
    assert first.domain_counts == {"opslab": 2, "supportlab": 2}
    assert first.framework_counts == {"autogen": 2, "langgraph": 2}
    assert first.condition_count == 24
    assert first.evaluated_count == 24
    assert set(first.condition_counts) == {
        "b0_no_verifier",
        "b1_deterministic",
        "b2_deepseek_shared",
        "b3_deepseek_isolated",
        "b4_qwen_isolated",
        "b5_deterministic_qwen",
    }
    assert first.provider_calls == 0
    assert first.gpu_calls == 0
    assert first.fake_evidence is True
    assert read_verified_directory_tree(tmp_path / "first" / "bundle").files == (
        read_verified_directory_tree(tmp_path / "second" / "bundle").files
    )


@pytest.mark.asyncio
async def test_committed_reference_bundle_reproduces_byte_for_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket.socket, "connect", _forbid_socket)
    monkeypatch.setattr(httpx.AsyncClient, "send", _forbid_http)

    generated = await run_offline_acceptance(tmp_path / "generated")
    committed = read_verified_directory_tree(REFERENCE)
    reproduced = read_verified_directory_tree(tmp_path / "generated" / "bundle")

    assert reproduced.files == committed.files
    assert reproduced.directories == committed.directories == frozenset()
    assert generated.bundle_manifest_sha256 == generated.reproduced_bundle_manifest_sha256
    readme = committed.files["README.md"].decode("utf-8").casefold()
    assert "fake-provider" in readme
    assert "not paper evidence" in readme
