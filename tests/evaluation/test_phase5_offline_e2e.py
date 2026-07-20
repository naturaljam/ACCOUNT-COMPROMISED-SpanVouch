from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from spanvouch.contracts.artifacts import CodeProvenance
from spanvouch.evaluation import offline_acceptance
from spanvouch.evaluation.artifacts import read_verified_directory_tree
from spanvouch.evaluation.offline_acceptance import run_offline_acceptance

REFERENCE = Path("evals/reports/reference/phase5-offline-smoke")
FIXTURE_CODE = CodeProvenance(
    git_commit="a" * 40,
    repository_identity="fixture:phase5-offline-smoke",
    dirty_worktree=False,
)


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

    first = await run_offline_acceptance(
        tmp_path / "first", code_provenance=FIXTURE_CODE
    )
    second = await run_offline_acceptance(
        tmp_path / "second", code_provenance=FIXTURE_CODE
    )

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
    metrics = json.loads((tmp_path / "first" / "bundle" / "metrics.json").read_text())
    condition_metrics = metrics["statistics"]["condition_metrics"]
    assert all(values["accepted_count"] == 4 for values in condition_metrics.values())
    assert all(values["coverage"] == 1.0 for values in condition_metrics.values())
    assert all(values["false_acceptance_risk"] == 1.0 for values in condition_metrics.values())
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

    committed = read_verified_directory_tree(REFERENCE)
    manifest = json.loads(committed.files["manifest.json"])
    generated = await run_offline_acceptance(
        tmp_path / "generated",
        code_provenance=CodeProvenance.model_validate(manifest["code"]),
    )
    reproduced = read_verified_directory_tree(tmp_path / "generated" / "bundle")

    assert reproduced.files == committed.files
    assert reproduced.directories == committed.directories == frozenset()
    assert generated.bundle_manifest_sha256 == generated.reproduced_bundle_manifest_sha256
    readme = committed.files["README.md"].decode("utf-8").casefold()
    assert "fake-provider" in readme
    assert "not paper evidence" in readme


def test_git_provenance_discovery_uses_safe_commands_and_full_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(command))
        assert kwargs["cwd"] == tmp_path
        assert kwargs["check"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 10
        stdout = "b" * 40 + "\n" if command[1] == "rev-parse" else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(offline_acceptance.subprocess, "run", run)
    discovered = offline_acceptance._discover_code_provenance(tmp_path)

    assert discovered.git_commit == "b" * 40
    assert discovered.dirty_worktree is False
    assert calls == [
        ("git", "rev-parse", "--verify", "HEAD"),
        ("git", "status", "--porcelain=v1", "--untracked-files=normal"),
    ]


@pytest.mark.parametrize(
    ("revision", "status", "message"),
    [
        ("short", "", "full 40-hex"),
        ("c" * 40, " M tracked.py\n", "clean Git worktree"),
    ],
)
def test_accepted_reference_rejects_invalid_head_or_dirty_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    revision: str,
    status: str,
    message: str,
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        stdout = revision + "\n" if command[1] == "rev-parse" else status
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(offline_acceptance.subprocess, "run", run)
    with pytest.raises(RuntimeError, match=message):
        offline_acceptance._accepted_code_provenance(None)
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_injected_dirty_provenance_is_rejected_before_output(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="clean Git worktree"):
        await run_offline_acceptance(
            tmp_path / "rejected",
            code_provenance=FIXTURE_CODE.model_copy(update={"dirty_worktree": True}),
        )
    assert not (tmp_path / "rejected").exists()
