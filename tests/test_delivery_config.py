from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_dataset_fixtures_are_checked_out_with_lf_endings() -> None:
    attributes_file = ROOT / ".gitattributes"
    assert attributes_file.is_file(), "repository .gitattributes must define fixture EOLs"

    datasets = (
        ROOT / "evals" / "datasets" / "supportlab-v1",
        ROOT / "evals" / "datasets" / "supportlab-review-v1",
    )
    fixture_paths = sorted(
        path
        for dataset in datasets
        for pattern in ("*.jsonl", "*.json")
        for path in dataset.glob(pattern)
    )
    relative_paths = [path.relative_to(ROOT).as_posix() for path in fixture_paths]
    result = subprocess.run(
        ["git", "check-attr", "text", "eol", "--", *relative_paths],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    expected = {
        f"{path}: {attribute}: {value}"
        for path in relative_paths
        for attribute, value in (("text", "set"), ("eol", "lf"))
    }
    assert set(result.stdout.splitlines()) == expected


def test_phase_3_verification_documents_exact_sqlite_process_gate() -> None:
    verification = (
        ROOT / "docs" / "evaluation" / "phase3-verification-review.md"
    ).read_text(encoding="utf-8")

    assert (
        r".\.venv\Scripts\python.exe -m pytest "
        r"tests/review/test_sqlite_process_stability.py -q"
    ) in verification


def test_phase_2_delivery_is_safe_and_reproducible() -> None:
    required_files = (
        ".env.example",
        "docs/evaluation/phase2-diagnosis-evaluation.md",
    )
    missing = [path for path in required_files if not (ROOT / path).is_file()]
    assert not missing, f"missing Phase 2 delivery files: {missing}"

    environment = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"(?m)^DEEPSEEK_API_KEY=$", environment)
    assert re.search(r"(?m)^DEEPSEEK_MODEL=deepseek-v4-flash$", environment)

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "evals/reports/generated/" in gitignore

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "afc-evaluate-diagnosis" in readme
    assert "--allow-live-api" in readme
    assert "POST /v1/traces/{trace_id}/diagnoses" in readme
    assert "rules" in readme and "DEEPSEEK_API_KEY" in readme


def test_phase_1_delivery_configuration_is_reproducible() -> None:
    required_files = (
        ".dockerignore",
        ".python-version",
        "Dockerfile",
        "compose.yaml",
        ".github/workflows/ci.yml",
        "README.md",
        "docs/architecture/adr-001-traceir-boundary.md",
    )
    missing = [path for path in required_files if not (ROOT / path).is_file()]
    assert not missing, f"missing Phase 1 delivery files: {missing}"

    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "arizephoenix/phoenix:latest" not in compose
    assert re.search(r"arizephoenix/phoenix@sha256:[0-9a-f]{64}", compose)

    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "Verify frozen dataset hashes" in workflow
    assert "docker compose config --quiet" in workflow


def test_api_image_is_immutable_unprivileged_and_minimal() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    base_images = re.findall(r"^FROM\s+(\S+)", dockerfile, flags=re.MULTILINE)

    assert len(base_images) >= 3
    assert all(re.search(r"@sha256:[0-9a-f]{64}$", image) for image in base_images)
    assert any(image.startswith("python:3.12") for image in base_images)
    assert any(image.startswith("ghcr.io/astral-sh/uv:0.8.15@") for image in base_images)

    runtime = dockerfile.rsplit("FROM ", maxsplit=1)[1]
    assert "USER 10001:10001" in runtime
    assert "--chown=10001:10001" in runtime
    assert "/opt/venv/bin/uvicorn" in runtime
    assert "COPY src" not in runtime
    assert "COPY --from=ghcr.io" not in runtime


def test_phase_3_sqlite_data_directory_is_owned_and_persisted() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    runtime = dockerfile.rsplit("FROM ", maxsplit=1)[1]
    user_offset = runtime.index("USER 10001:10001")

    assert "mkdir -p /app /data" in runtime[:user_offset]
    assert "chown 10001:10001 /app /data" in runtime[:user_offset]

    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    api_block, _, volumes_block = compose.partition("  phoenix:")
    assert re.search(r"(?m)^\s+AFC_DB_PATH:\s*/data/afc\.db$", api_block)
    assert re.search(r"(?m)^\s+- afc_data:/data$", api_block)
    assert re.search(r"(?m)^\s{2}afc_data:\s*$", volumes_block)

    environment = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"(?m)^AFC_DB_PATH=\.data/afc\.db$", environment)

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"(?m)^\.data/$", gitignore)
    assert re.search(r"(?m)^evals/reports/generated/$", gitignore)


def test_docker_build_context_excludes_local_secrets_and_runtime_data() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    patterns = set(dockerignore.splitlines())

    assert ".env" in patterns
    assert ".env.*" in patterns
    assert "!.env.example" in patterns
    assert ".data/" in patterns
    assert ".cache/" in patterns
    assert "evals/reports/generated/" in patterns


def test_phase_3_ci_regenerates_reviews_and_proves_restart_recovery() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    required_fragments = (
        "afc-generate-review-dataset",
        "Verify frozen review dataset hashes",
        "afc-evaluate-review --output .cache/ci-review-a.json",
        "afc-evaluate-review --output .cache/ci-review-b.json",
        "cmp --silent .cache/ci-review-a.json .cache/ci-review-b.json",
        "docker compose restart api",
        "afc-review show",
        "afc-review decide",
        'id -u):$(id -g)" = "10001:10001',
        "stat -c %u:%g /data",
        "docker compose down --volumes --remove-orphans",
    )
    missing = [fragment for fragment in required_fragments if fragment not in workflow]
    assert not missing, f"missing Phase 3 CI persistence gates: {missing}"

    assert "DEEPSEEK_API_KEY" not in workflow
    assert "--allow-live-api" not in workflow

    cleanup = workflow.split("          cleanup() {", maxsplit=1)[1].split(
        "          trap cleanup EXIT", maxsplit=1
    )[0]
    failure_branch = cleanup.split('if [ "$status" -ne 0 ]; then', maxsplit=1)[1].split(
        "            fi", maxsplit=1
    )[0]
    assert "docker compose logs --no-color api" in failure_branch
    assert "docker compose down --remove-orphans || true" in cleanup
    assert "docker compose down --volumes" not in cleanup
    assert 'exit "$status"' in cleanup

    trap = workflow.index("          trap cleanup EXIT")
    final_audit_assertion = workflow.index(
        'assert [event["event_sequence"] for event in payload["events"]]'
    )
    trap_disabled = workflow.index("          trap - EXIT")
    destructive_cleanup = workflow.index(
        "          docker compose down --volumes --remove-orphans"
    )
    destructive_line = workflow[destructive_cleanup:].splitlines()[0]
    assert trap < final_audit_assertion < trap_disabled < destructive_cleanup
    assert "|| true" not in destructive_line
    assert workflow.count("docker compose down --volumes --remove-orphans") == 1


def test_readme_offline_review_walkthrough_uses_a_frozen_trace_end_to_end() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    required_fragments = (
        "evals/datasets/supportlab-v1/traces.jsonl",
        "POST /v1/traces",
        'trace_id="$(',
        "--data-binary @.cache/afc-demo-trace.json",
        'created="$(uv run afc-review create',
        'case_id="$(python',
        'version="$(python',
        'uv run afc-review show --case-id "$case_id"',
        '--expected-version "$version"',
    )
    missing = [fragment for fragment in required_fragments if fragment not in readme]
    assert not missing, f"README offline walkthrough is not reproducible: {missing}"


def test_python_patch_is_shared_by_ci_and_docker() -> None:
    version_file = ROOT / ".python-version"
    assert version_file.is_file()
    python_version = version_file.read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"3\.12\.\d+", python_version)

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    docker_python_versions = re.findall(
        r"^FROM\s+python:([^\s@-]+)-slim@sha256:", dockerfile, flags=re.MULTILINE
    )
    assert docker_python_versions
    assert set(docker_python_versions) == {python_version}

    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert 'python-version-file: ".python-version"' in workflow
    assert not re.search(r"^\s+python-version:\s*", workflow, flags=re.MULTILINE)


def test_api_build_backend_is_fully_hash_locked() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    build_system = pyproject["build-system"]
    backend_distribution = build_system["build-backend"].partition(".")[0]
    build_requirements = build_system["requires"]
    assert len(build_requirements) == 1
    backend_requirement = build_requirements[0]
    assert re.fullmatch(rf"{re.escape(backend_distribution)}==[^=<>!~\s]+", backend_requirement)

    constraints_input = (ROOT / "build-constraints.in").read_text(encoding="utf-8").strip()
    assert constraints_input == backend_requirement

    constraints_path = ROOT / "build-constraints.txt"
    assert constraints_path.is_file()

    constraints = constraints_path.read_text(encoding="utf-8")
    requirement_starts = list(re.finditer(r"(?m)^[a-zA-Z0-9_.-]+==[^\s\\]+", constraints))
    assert requirement_starts
    assert requirement_starts[0].group() == backend_requirement

    for index, match in enumerate(requirement_starts):
        end = requirement_starts[index + 1].start() if index + 1 < len(requirement_starts) else None
        requirement_block = constraints[match.start() : end]
        assert "--hash=sha256:" in requirement_block

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compact_dockerfile = " ".join(dockerfile.split())
    assert "COPY pyproject.toml uv.lock README.md build-constraints.txt ./" in compact_dockerfile
    assert (
        "uv build --wheel --build-constraints build-constraints.txt --require-hashes --no-cache"
        in compact_dockerfile
    )
    assert "uv sync --frozen --no-dev --no-install-project --no-cache" in compact_dockerfile
    assert (
        "uv pip install --python /opt/venv/bin/python --no-deps --no-cache dist/*.whl"
        in compact_dockerfile
    )


def test_compose_defines_executable_phoenix_healthcheck() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "http://localhost:6006/healthz" in compose
    assert compose.count("healthcheck:") == 2


def test_ci_pins_platform_inputs_and_smoke_tests_api_image() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    action_lines = [line.strip() for line in workflow.splitlines() if "uses:" in line]

    assert "runs-on: ubuntu-24.04" in workflow
    assert action_lines
    assert all(re.search(r"@[0-9a-f]{40}\s+#\s+v\d", line) for line in action_lines)
    assert "docker compose build api" in workflow
    assert "docker compose up --detach --wait --wait-timeout" in workflow
    assert "curl --fail" in workflow
    assert "docker compose logs --no-color api" in workflow
    assert "docker compose down --volumes --remove-orphans" in workflow


def test_ci_builds_and_installs_the_hash_constrained_wheel_before_checks() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    run_commands = re.findall(r"^\s+- run:\s+(.+)$", workflow, flags=re.MULTILINE)

    protected_install = [
        "uv sync --frozen --group dev --no-install-project",
        "uv build --wheel --build-constraints build-constraints.txt --require-hashes --no-cache",
        "uv pip install --python .venv/bin/python --no-deps --no-cache dist/*.whl",
    ]
    assert all(command in run_commands for command in protected_install)
    protected_indices = [run_commands.index(command) for command in protected_install]
    assert protected_indices == sorted(protected_indices)

    project_commands = [command for command in run_commands if command.startswith("uv run ")]
    assert project_commands
    assert protected_indices[-1] < min(run_commands.index(command) for command in project_commands)
    assert all(command.startswith("uv run --no-sync ") for command in project_commands)
