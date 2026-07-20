from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path

import pytest

import spanvouch.evaluation.artifacts as artifacts_module
from spanvouch.contracts.artifacts import ArtifactRef, ModelProvenance, UsageProvenance
from spanvouch.contracts.versioning import canonical_sha256
from spanvouch.evaluation.artifacts import ArtifactBundleWriter, collect_git_provenance


def test_bundle_writer_hashes_every_required_file(
    tmp_path: Path, artifact_manifest: object
) -> None:
    bundle = tmp_path / "bundle"
    writer = ArtifactBundleWriter(bundle)
    written = writer.write(
        manifest=artifact_manifest,
        config={"mode": "deterministic"},
        metrics={"status": "complete"},
        structured_events=(),
        environment="python=3.12\n",
        readme="# Reproduce\n",
    )
    assert set(path.name for path in written) == {
        "manifest.json",
        "config.json",
        "metrics.json",
        "structured-events.jsonl",
        "environment.txt",
        "README.md",
    }
    assert canonical_sha256({"mode": "deterministic"}) == next(
        ref.sha256 for ref in artifact_manifest.inputs if ref.path == "config.json"
    )


def test_bundle_writer_rejects_hash_mismatch_and_cleans_temporary_directory(
    tmp_path: Path, artifact_manifest: object
) -> None:
    bad_manifest = artifact_manifest.model_copy(
        update={
            "configuration": artifact_manifest.configuration.model_copy(
                update={"sha256": "a" * 64}
            )
        }
    )
    with pytest.raises(ValueError, match="artifact SHA-256 mismatch"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=bad_manifest,
            config={"mode": "deterministic"},
            metrics={"status": "complete"},
            structured_events=(),
            environment="python=3.12\n",
            readme="# Reproduce\n",
        )
    assert not list(tmp_path.iterdir())


def test_bundle_writer_refuses_to_overwrite_destination(
    tmp_path: Path, artifact_manifest: object
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    with pytest.raises(FileExistsError, match="destination already exists"):
        ArtifactBundleWriter(bundle).write(
            manifest=artifact_manifest,
            config={"mode": "deterministic"},
            metrics={"status": "complete"},
            structured_events=(),
            environment="python=3.12\n",
            readme="# Reproduce\n",
        )


def test_bundle_writer_requires_declared_refs_to_cover_exact_generated_files(
    tmp_path: Path, artifact_manifest: object
) -> None:
    incomplete_manifest = artifact_manifest.model_copy(
        update={"outputs": artifact_manifest.outputs[:-1]}
    )
    with pytest.raises(ValueError, match="declared refs"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=incomplete_manifest,
            config={"mode": "deterministic"},
            metrics={"status": "complete"},
            structured_events=(),
            environment="python=3.12\n",
            readme="# Reproduce\n",
        )


def test_bundle_writer_rejects_undeclared_external_input(
    tmp_path: Path, artifact_manifest: object
) -> None:
    external_ref = ArtifactRef(
        path="outside.json", sha256="a" * 64, media_type="application/json"
    )
    invalid_manifest = artifact_manifest.model_copy(
        update={"inputs": (artifact_manifest.inputs[0], external_ref)}
    )
    with pytest.raises(ValueError, match="declared refs"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=invalid_manifest,
            config={"mode": "deterministic"},
            metrics={"status": "complete"},
            structured_events=(),
            environment="python=3.12\n",
            readme="# Reproduce\n",
        )


def test_collect_git_provenance_records_non_secret_local_identity() -> None:
    provenance = collect_git_provenance(Path.cwd())
    assert len(provenance.git_commit) == 40
    assert provenance.repository_identity == "local:phase4-integration"


def test_bundle_writer_permits_provenance_hashes_but_rejects_raw_secrets(
    tmp_path: Path, artifact_manifest: object
) -> None:
    used_manifest = artifact_manifest.model_copy(
        update={
            "provider_status": "used",
            "models": (
                ModelProvenance(
                    provider="deepseek",
                    model="deepseek-chat",
                    endpoint_class="chat.completions",
                    generation_config_sha256="a" * 64,
                    prompt_sha256="b" * 64,
                ),
            ),
            "usage": UsageProvenance(
                requests=1, input_tokens=2, output_tokens=3, total_tokens=5
            ),
        }
    )
    with pytest.raises(ValueError, match="unsafe artifact content"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=used_manifest,
            config={"mode": "deterministic"},
            metrics={"status": "complete"},
            structured_events=(),
            environment="token=sk-12345678",
            readme="# Reproduce\n",
        )


@pytest.mark.parametrize(
    ("structured_events", "environment"),
    (
        (({"provider_body": "private response"},), "python=3.12"),
        ((), "DEEPSEEK_API_KEY=artifact-secret-sentinel"),
    ),
)
def test_bundle_writer_rejects_provider_bodies_and_environment_values(
    tmp_path: Path,
    artifact_manifest: object,
    structured_events: tuple[object, ...],
    environment: str,
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=artifact_manifest,
            config={"mode": "deterministic"},
            metrics={"status": "complete"},
            structured_events=structured_events,
            environment=environment,
            readme="# Reproduce\n",
        )


@pytest.mark.parametrize(
    ("config", "metrics", "structured_events", "environment"),
    (
        ({"system_prompt": "do not persist me"}, {"status": "complete"}, (), "python=3.12"),
        ({"mode": "deterministic"}, {"response_raw": "provider body"}, (), "python=3.12"),
        (
            {"mode": "deterministic"},
            {"status": "complete"},
            ({"headers": {"Authorization": "Bearer private"}},),
            "python=3.12",
        ),
        ({"mode": "deterministic"}, {"token": "private"}, (), "python=3.12"),
        ({"mode": "deterministic"}, {"password": "private"}, (), "python=3.12"),
        ({"mode": "deterministic"}, {"hidden reasoning": "private"}, (), "python=3.12"),
        (
            {"mode": "deterministic"},
            {"status": "complete"},
            (),
            "AWS_SECRET_ACCESS_KEY=private",
        ),
    ),
)
def test_bundle_writer_rejects_sensitive_structured_content_before_hashing(
    tmp_path: Path,
    artifact_manifest: object,
    config: object,
    metrics: object,
    structured_events: tuple[object, ...],
    environment: str,
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content") as raised:
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=artifact_manifest,
            config=config,
            metrics=metrics,
            structured_events=structured_events,
            environment=environment,
            readme="# Reproduce\n",
        )
    assert "private" not in str(raised.value)


def test_bundle_writer_rejects_unknown_config_keys_before_hashing(
    tmp_path: Path, artifact_manifest: object
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=artifact_manifest,
            config={"unreviewed_nested_option": {"value": True}},
            metrics={"status": "complete"},
            structured_events=(),
            environment="python=3.12",
            readme="# Reproduce\n",
        )


def test_bundle_writer_accepts_the_task15_reference_config(
    tmp_path: Path, artifact_manifest: object
) -> None:
    reference_config = {
        "schema_version": "1.0",
        "dataset": "evals/datasets/supportlab-review-v1",
        "source_dataset": "evals/datasets/supportlab-v1",
        "verifier": "deterministic",
        "policy_version": "supportlab-review-policy-v1",
        "seed": 20260717,
        "allow_live_api": False,
    }
    reference = ArtifactRef(
        path="config.json",
        sha256=canonical_sha256(reference_config),
        media_type="application/json",
    )
    manifest = artifact_manifest.model_copy(
        update={"configuration": reference, "inputs": (reference,)}
    )
    written = ArtifactBundleWriter(tmp_path / "bundle").write(
        manifest=manifest,
        config=reference_config,
        metrics={"status": "complete"},
        structured_events=(),
        environment="python=3.12",
        readme="# Reproduce\n",
    )
    assert (tmp_path / "bundle" / "config.json") in written


@pytest.mark.parametrize(
    ("config", "environment", "readme"),
    (
        ([], "python=3.12", "# Reproduce"),
        ({"mode": ""}, "python=3.12", "# Reproduce"),
        ({"seed": True}, "python=3.12", "# Reproduce"),
        ({"allow_live_api": "false"}, "python=3.12", "# Reproduce"),
        ({"mode": "deterministic"}, "", "# Reproduce"),
        ({"mode": "deterministic"}, "python=3.12", "Bearer private"),
    ),
)
def test_bundle_writer_rejects_invalid_safe_content_shapes(
    tmp_path: Path,
    artifact_manifest: object,
    config: object,
    environment: str,
    readme: str,
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=artifact_manifest,
            config=config,
            metrics={"status": "complete"},
            structured_events=(),
            environment=environment,
            readme=readme,
        )


def test_bundle_writer_publishes_exactly_once_under_concurrent_writers(
    tmp_path: Path, artifact_manifest: object
) -> None:
    destination = tmp_path / "bundle"

    def write_once() -> str:
        try:
            ArtifactBundleWriter(destination).write(
                manifest=artifact_manifest,
                config={"mode": "deterministic"},
                metrics={"status": "complete"},
                structured_events=(),
                environment="python=3.12",
                readme="# Reproduce\n",
            )
        except FileExistsError:
            return "exists"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: write_once(), range(2)))
    assert sorted(results) == ["exists", "published"]
    assert {path.name for path in destination.iterdir()} == {
        "manifest.json",
        "config.json",
        "metrics.json",
        "structured-events.jsonl",
        "environment.txt",
        "README.md",
    }


def test_publish_fails_closed_when_atomic_no_replace_is_unsupported(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(RuntimeError, match="atomic no-replace"):
        artifacts_module._publish_no_replace(source, tmp_path / "destination", platform="other")


@pytest.mark.parametrize(
    ("target", "value"),
    (
        ("environment", "repository_identity=ghp_0123456789abcdefghijklmnopqrstuv"),
        ("metrics", {"credential_url": "https://user:password@example.invalid/report"}),
        ("metrics", {"authentication": "Basic dXNlcjpwYXNzd29yZA=="}),
        ("metrics", {"accessKey": "AKIAIOSFODNN7EXAMPLE"}),
        ("events", ({"api-key": "sk-proj-0123456789abcdefghijklmnop"},)),
        ("events", ({"sessionToken": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"},)),
        ("readme", "-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----"),
        ("readme", "glpat-0123456789abcdefghijkl"),
        ("readme", "xoxb-0123456789-abcdefghijklmnopqrst"),
        ("readme", "opaque: aB3dE5fG7hJ9kLmNpQrStUvWxYz0123456789AbCdEfGhIjKlMn"),
    ),
)
def test_bundle_writer_rejects_credential_shaped_values_in_every_surface(
    tmp_path: Path, artifact_manifest: object, target: str, value: object
) -> None:
    config: object = {"mode": "deterministic"}
    metrics: object = {"status": "complete"}
    events: tuple[object, ...] = ()
    environment = "python=3.12"
    readme = "# Reproduce\n"
    if target == "environment":
        environment = str(value)
    elif target == "metrics":
        metrics = value
    elif target == "events":
        events = value
    else:
        readme = str(value)

    with pytest.raises(ValueError, match="unsafe artifact content") as raised:
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=artifact_manifest,
            config=config,
            metrics=metrics,
            structured_events=events,
            environment=environment,
            readme=readme,
        )
    assert str(value) not in str(raised.value)


def test_bundle_writer_preserves_declared_safe_provenance_and_reproduction_text(
    tmp_path: Path, artifact_manifest: object
) -> None:
    metrics = {
        "git_commit": "a" * 40,
        "report_sha256": "b" * 64,
        "version": "1.0",
        "status": "complete",
    }
    environment = "python=3.12\ngit_commit=" + "a" * 40
    readme = "Run the offline evaluation from evals/datasets/supportlab-v1.\n"
    digests = {
        "metrics.json": canonical_sha256(metrics),
        "environment.txt": sha256((environment + "\n").encode()).hexdigest(),
        "README.md": sha256(readme.encode()).hexdigest(),
    }
    manifest = artifact_manifest.model_copy(
        update={
            "outputs": tuple(
                reference.model_copy(update={"sha256": digests[reference.path]})
                if reference.path in digests
                else reference
                for reference in artifact_manifest.outputs
            )
        }
    )
    written = ArtifactBundleWriter(tmp_path / "bundle").write(
        manifest=manifest,
        config={"mode": "deterministic"},
        metrics=metrics,
        structured_events=(),
        environment=environment,
        readme=readme,
    )
    assert (tmp_path / "bundle" / "README.md") in written


def test_secret_classifier_rejects_url_userinfo_even_without_a_sensitive_key() -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        artifacts_module.ArtifactSecretClassifier().require_safe(
            "https://user:password@example.invalid/report"
        )


def test_secret_classifier_accepts_short_pathless_text() -> None:
    artifacts_module.ArtifactSecretClassifier().require_safe("offline evaluation")


@pytest.mark.parametrize("key", ("apiKey", "api_key", "api-key", "api_key_sha256"))
def test_secret_classifier_rejects_sensitive_key_variants_with_short_values(key: str) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        artifacts_module.ArtifactSecretClassifier().require_safe({key: "ok"})


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("config", "dataset"), "evals/ghp_0123456789abcdefghijklmnopqrstuv"),
        (("metrics", "note"), "qwertyuiopasdfghjklzxcvbnmabcdef"),
        (("readme",), "a lower-case opaque qwertyuiopasdfghjklzxcvbnmabcdef token"),
    ),
)
def test_secret_classifier_rejects_credentials_before_context_exceptions(
    path: tuple[str, ...], value: str
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        artifacts_module.ArtifactSecretClassifier().require_safe(value, path=path)


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("manifest", "code", "git_commit"), "a" * 40),
        (("manifest", "configuration", "sha256"), "b" * 64),
        (("config", "schema_version"), "1.0"),
        (("config", "dataset"), "evals/datasets/supportlab-v1"),
        (("manifest", "outputs", "path"), "metrics.json"),
        (("metrics", "status"), "complete"),
        (("manifest", "configuration", "media_type"), "application/json"),
    ),
)
def test_secret_classifier_allows_only_contextual_safe_values(
    path: tuple[str, ...], value: str
) -> None:
    artifacts_module.ArtifactSecretClassifier().require_safe(value, path=path)


def test_bundle_writer_rejects_path_embedded_credential_before_hashing(
    tmp_path: Path, artifact_manifest: object
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=artifact_manifest,
            config={"dataset": "evals/ghp_0123456789abcdefghijklmnopqrstuv"},
            metrics={"status": "complete"},
            structured_events=(),
            environment="python=3.12",
            readme="# Reproduce\n",
        )


_LOWERCASE_OPAQUE_TOKEN = "qwertyuiopasdfghjklzxcvbnmabcdef"


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("config", "dataset"), f"evals/{_LOWERCASE_OPAQUE_TOKEN}"),
        (("metrics", "status"), _LOWERCASE_OPAQUE_TOKEN),
        (("manifest", "artifact_id"), _LOWERCASE_OPAQUE_TOKEN),
        (("manifest", "configuration", "media_type"), f"application/{_LOWERCASE_OPAQUE_TOKEN}"),
        (("manifest", "outputs", "path"), f"reports/{_LOWERCASE_OPAQUE_TOKEN}.json"),
    ),
)
def test_secret_classifier_scans_opaque_atoms_before_semantic_acceptance(
    path: tuple[str, ...], value: str
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        artifacts_module.ArtifactSecretClassifier().require_safe(value, path=path)


@pytest.mark.parametrize(
    "event",
    (
        {"metrics": {"samples": [{"candidate_id": _LOWERCASE_OPAQUE_TOKEN}]}},
        {
            "metrics": {
                "samples": [
                    {"report": {"evidence": {"observed_value": _LOWERCASE_OPAQUE_TOKEN}}}
                ]
            }
        },
    ),
)
def test_secret_classifier_does_not_lend_metrics_exemptions_to_events(
    event: object,
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        artifacts_module.ArtifactSecretClassifier().require_safe(
            event, path=("structured_events",)
        )


@pytest.mark.parametrize(
    "path",
    (
        ("metrics", "untrusted", "candidate_id"),
        ("metrics", "untrusted", "report_sha256"),
    ),
)
def test_secret_classifier_requires_exact_metrics_field_paths(
    path: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        artifacts_module.ArtifactSecretClassifier().require_safe(
            (
                _LOWERCASE_OPAQUE_TOKEN
                if path[-1] == "candidate_id"
                else "0123456789abcdef" * 4
            ),
            path=path,
        )


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("corpus_record", "trace", "trace_id"), "0123456789abcdef" * 2),
        (("corpus_record", "trace", "spans", "0", "trace_id"), "0123456789abcdef" * 2),
        (("corpus_record", "trace", "spans", "0", "span_id"), "0123456789abcdef"),
        (("corpus_record", "trace", "spans", "0", "parent_span_id"), "0123456789abcdef"),
        (("corpus_trace", "trace_id"), "0123456789abcdef" * 2),
        (("corpus_trace", "spans", "0", "trace_id"), "0123456789abcdef" * 2),
        (("corpus_trace", "spans", "0", "span_id"), "0123456789abcdef"),
        (("corpus_trace", "spans", "0", "parent_span_id"), "0123456789abcdef"),
        (
            ("corpus_parity_results", "0", "result", "mismatches", "0", "reference_sha256"),
            "0123456789abcdef" * 4,
        ),
        (
            ("corpus_parity_results", "0", "result", "mismatches", "0", "candidate_sha256"),
            "0123456789abcdef" * 4,
        ),
        (("corpus_record", "failure", "error_sha256"), "0123456789abcdef" * 4),
    ),
)
def test_secret_classifier_allows_exact_corpus_otel_identifier_paths(
    path: tuple[str, ...], value: str
) -> None:
    artifacts_module.ArtifactSecretClassifier().require_safe(value, path=path)


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (
            ("corpus_record", "trace", "spans", "attributes", "trace_id"),
            "0123456789abcdef" * 2,
        ),
        (
            ("corpus_trace", "spans", "attributes", "span_id"),
            "0123456789abcdef" * 2,
        ),
        (
            ("corpus_record", "trace", "trace_id"),
            "sk-" + "0123456789abcdefghijklmnop",
        ),
        (
            ("corpus_record", "trace", "spans", "attributes", "reference_sha256"),
            "0123456789abcdef" * 4,
        ),
    ),
)
def test_secret_classifier_keeps_corpus_identifier_exemptions_narrow(
    path: tuple[str, ...], value: str
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        artifacts_module.ArtifactSecretClassifier().require_safe(value, path=path)


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (
            ("corpus_manifest", "parity_entries", "0", "pair_identity"),
            "supportlab:missing_precondition-01:missing_precondition-01:1:20260719",
        ),
        (
            ("corpus_parity_results", "0", "pair_identity"),
            "opslab:timeout-no-retry:timeout-no-retry:3:20260781",
        ),
        (
            ("corpus_manifest", "parity_entries", "0", "result_path"),
            f"parity/sha256/{'a' * 64}.json",
        ),
        (
            ("corpus_manifest", "parity_entries", "0", "result_sha256"),
            "a" * 64,
        ),
        (
            ("corpus_manifest", "parity_payloads_sha256"),
            "a" * 64,
        ),
    ),
)
def test_secret_classifier_allows_only_typed_phase5_parity_identifiers(
    path: tuple[str, ...], value: str
) -> None:
    artifacts_module.ArtifactSecretClassifier().require_safe(value, path=path)


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (
            ("corpus_manifest", "parity_entries", "pair_identity"),
            "supportlab:scenario:1:" + _LOWERCASE_OPAQUE_TOKEN,
        ),
        (
            ("corpus_manifest", "parity_entries", "result_path"),
            f"labels/sha256/{_LOWERCASE_OPAQUE_TOKEN}.json",
        ),
        (
            ("corpus_record", "pair_identity"),
            _LOWERCASE_OPAQUE_TOKEN,
        ),
    ),
)
def test_secret_classifier_keeps_phase5_parity_exemptions_path_and_grammar_exact(
    path: tuple[str, ...], value: str
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        artifacts_module.ArtifactSecretClassifier().require_safe(value, path=path)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("result_sha256", "0123456789abcdef" * 4),
        ("parity_payloads_sha256", "0123456789abcdef" * 4),
        ("result_sha256", _LOWERCASE_OPAQUE_TOKEN),
        ("parity_payloads_sha256", _LOWERCASE_OPAQUE_TOKEN),
        ("result_sha256", "sk-" + "0123456789abcdefghijklmnop"),
        ("parity_payloads_sha256", "sk-" + "0123456789abcdefghijklmnop"),
    ),
)
def test_secret_classifier_rejects_parity_hash_names_at_arbitrary_trace_depth(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        artifacts_module.ArtifactSecretClassifier().require_safe(
            value,
            path=("corpus_trace", "spans", "0", "attributes", field),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("record_sha256", "0123456789abcdef" * 4),
        ("trace_sha256", "fedcba9876543210" * 4),
        ("result_sha256", "123456789abcdef0" * 4),
        ("parity_payloads_sha256", "abcdef0123456789" * 4),
        (
            "pair_identity",
            "supportlab:missing_precondition-01:missing_precondition-01:1:20260719",
        ),
        ("record_path", f"records/sha256/{'0123456789abcdef' * 4}.json"),
        ("trace_path", f"traces/sha256/{'fedcba9876543210' * 4}.json"),
        ("result_path", f"parity/sha256/{'123456789abcdef0' * 4}.json"),
        ("git_commit", "0123456789abcdef0123456789abcdef01234567"),
        ("trace_id", "0123456789abcdef" * 2),
        ("span_id", "0123456789abcdef"),
    ),
)
def test_secret_classifier_rejects_valid_corpus_identifiers_at_wrong_depth(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        artifacts_module.ArtifactSecretClassifier().require_safe(
            value,
            path=("corpus_record", "trace", "spans", "0", "attributes", field),
        )


@pytest.mark.parametrize(
    "path",
    (
        ("corpus_record", "trace", "spans", "0", "attributes", "tool.result"),
        ("corpus_trace", "spans", "0", "attributes", "tool.result"),
    ),
)
def test_secret_classifier_allows_only_sanitized_refund_results_in_corpus_traces(
    path: tuple[str, ...],
) -> None:
    value = (
        "refund_id='4f4de871-76f9-5f8f-8bef-86a2eb35a500' "
        "order_id='order-001' amount=Decimal('19.99') reason='damaged item' "
        "idempotency_key='missing_precondition-01-refund' "
        "approved_by='reviewer@example.test'"
    )
    artifacts_module.ArtifactSecretClassifier().require_safe(value, path=path)


@pytest.mark.parametrize(
    "value",
    (
        "qwertyuiopasdfghjklzxcvbnmabcdef",
        "sk-" + "0123456789abcdefghijklmnop",
    ),
)
def test_secret_classifier_rejects_unstructured_corpus_tool_results(value: str) -> None:
    with pytest.raises(ValueError, match="unsafe artifact content"):
        artifacts_module.ArtifactSecretClassifier().require_safe(
            value,
            path=("corpus_trace", "spans", "attributes", "tool.result"),
        )


def test_bundle_writer_rejects_event_that_nests_a_metrics_shaped_bypass(
    tmp_path: Path, artifact_manifest: object
) -> None:
    event = {"metrics": {"samples": [{"candidate_id": _LOWERCASE_OPAQUE_TOKEN}]}}
    with pytest.raises(ValueError, match="unsafe artifact content"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=artifact_manifest,
            config={"mode": "deterministic"},
            metrics={"status": "complete"},
            structured_events=(event,),
            environment="python=3.12",
            readme="# Reproduce\n",
        )


@pytest.mark.parametrize("surface", ("config_dataset", "metrics_status"))
def test_bundle_writer_rejects_opaque_semantic_values_before_hashing(
    tmp_path: Path, artifact_manifest: object, surface: str
) -> None:
    config = {"mode": "deterministic"}
    metrics = {"status": "complete"}
    manifest = artifact_manifest
    if surface == "config_dataset":
        config = {"dataset": f"evals/{_LOWERCASE_OPAQUE_TOKEN}"}
        reference = artifact_manifest.configuration.model_copy(
            update={"sha256": canonical_sha256(config)}
        )
        manifest = artifact_manifest.model_copy(
            update={"configuration": reference, "inputs": (reference,)}
        )
    else:
        metrics = {"status": _LOWERCASE_OPAQUE_TOKEN}
        digest = canonical_sha256(metrics)
        manifest = artifact_manifest.model_copy(
            update={
                "outputs": tuple(
                    reference.model_copy(update={"sha256": digest})
                    if reference.path == "metrics.json"
                    else reference
                    for reference in artifact_manifest.outputs
                )
            }
        )

    with pytest.raises(ValueError, match="unsafe artifact content"):
        ArtifactBundleWriter(tmp_path / "bundle").write(
            manifest=manifest,
            config=config,
            metrics=metrics,
            structured_events=(),
            environment="python=3.12",
            readme="# Reproduce\n",
        )
