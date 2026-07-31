from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from spanvouch.audit.export import create_audit_export, verify_audit_export
from tests.audit.test_export import _bootstrap_audit_events, _write_signing_key


def _bundle(tmp_path: Path) -> Path:
    project_id, events = _bootstrap_audit_events(tmp_path / "audit.db")
    signing_key_path = tmp_path / "audit-signing-key.pem"
    _write_signing_key(signing_key_path)
    return create_audit_export(
        project_id,
        tmp_path / "bundle",
        events=events,
        checkpoints=(),
        signing_key_path=signing_key_path,
    )


def test_verifier_rejects_tampered_events(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    events_path = bundle / "events.jsonl"
    events_path.write_text(
        events_path.read_text(encoding="utf-8").replace("trace.ingest", "trace.delete"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        verify_audit_export(bundle)


def test_verifier_rejects_tampered_checkpoint_signature(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    checkpoints_path = bundle / "checkpoints.json"
    checkpoints = json.loads(checkpoints_path.read_text(encoding="utf-8"))
    checkpoints[0]["signature_b64"] = "AA" + checkpoints[0]["signature_b64"][2:]
    checkpoints_path.write_text(json.dumps(checkpoints), encoding="utf-8")

    with pytest.raises(ValueError):
        verify_audit_export(bundle)


def test_verifier_rejects_public_key_replacement(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    replacement = Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    (bundle / "public-key.pem").write_bytes(replacement)

    with pytest.raises(ValueError):
        verify_audit_export(bundle)
