from __future__ import annotations

import json
from pathlib import Path

import pytest

from spanvouch.contracts.versioning import canonical_json, canonical_sha256
from spanvouch.evaluation.paper_assets import Phase5AnalysisInput
from spanvouch.evaluation.run_phase5_analysis import main
from tests.evaluation.statistics.test_claims import _evidence
from tests.evaluation.test_paper_assets import (
    _BundleConfig,
    analysis_input,
    bundle_config,
    claim_report,
)


def _validate_config(payload: object) -> _BundleConfig:
    return _BundleConfig.model_validate(payload)


def _evaluate_claims(payload: object) -> object:
    assert payload == {"fixture": True}
    return claim_report()


def _write_inputs(
    root: Path, *, analysis_override: Phase5AnalysisInput | None = None
) -> tuple[Path, Path]:
    evaluated = root / "evaluated"
    evaluated.mkdir()
    analysis_payload = (analysis_override or analysis_input()).model_dump(mode="json")
    (evaluated / "analysis-input.json").write_text(
        canonical_json(analysis_payload) + "\n", encoding="utf-8"
    )
    manifest = {
        "schema_name": "spanvouch.evaluated-results-analysis-manifest",
        "schema_version": "1.0",
        "analysis_input_sha256": canonical_sha256(analysis_payload),
    }
    (evaluated / "manifest.json").write_text(
        canonical_json(manifest) + "\n", encoding="utf-8"
    )
    config = root / "config.json"
    config.write_text(
        canonical_json(
            bundle_config(evaluated_results_sha256=canonical_sha256(manifest))
        )
        + "\n",
        encoding="utf-8",
    )
    return evaluated, config


def test_offline_analysis_cli_regenerates_identical_assets(tmp_path: Path) -> None:
    evaluated, config = _write_inputs(tmp_path)
    outputs = (tmp_path / "first", tmp_path / "second")
    for output in outputs:
        assert (
            main(
                [
                    "--evaluated-results",
                    str(evaluated),
                    "--config",
                    str(config),
                    "--output-dir",
                    str(output),
                ],
                bundle_config_validator=_validate_config,
                claim_evaluator=_evaluate_claims,
            )
            == 0
        )

    assert {path.name: path.read_bytes() for path in outputs[0].iterdir()} == {
        path.name: path.read_bytes() for path in outputs[1].iterdir()
    }


def test_default_cli_seam_uses_real_phase5_config_and_claim_gates(tmp_path: Path) -> None:
    analysis = analysis_input().model_copy(
        update={"claim_gate_evidence": _evidence().model_dump(mode="json")}
    )
    evaluated, config = _write_inputs(tmp_path, analysis_override=analysis)
    output = tmp_path / "real-output"

    assert main(
        [
            "--evaluated-results",
            str(evaluated),
            "--config",
            str(config),
            "--output-dir",
            str(output),
        ]
    ) == 0

    decisions = json.loads((output / "claim-gates.json").read_text("utf-8"))[
        "decisions"
    ]
    assert [item["hypothesis_id"] for item in decisions] == ["H1", "H2", "H3", "H4", "H5"]


def test_analysis_rejects_hash_drift_before_writing(tmp_path: Path) -> None:
    evaluated, config = _write_inputs(tmp_path)
    payload = json.loads(config.read_text("utf-8"))
    payload["evaluated_results_manifest_sha256"] = "f" * 64
    config.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "output"

    with pytest.raises(ValueError, match="evaluated-results manifest"):
        main(
            [
                "--evaluated-results",
                str(evaluated),
                "--config",
                str(config),
                "--output-dir",
                str(output),
            ],
            bundle_config_validator=_validate_config,
            claim_evaluator=_evaluate_claims,
        )

    assert not output.exists()


def test_analysis_rejects_payload_not_bound_by_evaluated_manifest(tmp_path: Path) -> None:
    evaluated, config = _write_inputs(tmp_path)
    payload_path = evaluated / "analysis-input.json"
    payload = json.loads(payload_path.read_text("utf-8"))
    payload["observations"][0]["observation"]["confidence"] = 0.123
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="analysis input SHA-256"):
        main(
            [
                "--evaluated-results",
                str(evaluated),
                "--config",
                str(config),
                "--output-dir",
                str(tmp_path / "output"),
            ],
            bundle_config_validator=_validate_config,
            claim_evaluator=_evaluate_claims,
        )


@pytest.mark.parametrize(
    "forbidden",
    ("--provider", "--endpoint", "--api-key", "--allow-live-provider", "--labels"),
)
def test_analysis_cli_has_no_live_or_label_arguments(
    tmp_path: Path, forbidden: str
) -> None:
    evaluated, config = _write_inputs(tmp_path)
    with pytest.raises(SystemExit, match="2"):
        main(
            [
                "--evaluated-results",
                str(evaluated),
                "--config",
                str(config),
                "--output-dir",
                str(tmp_path / "output"),
                forbidden,
                "sentinel",
            ],
            bundle_config_validator=_validate_config,
            claim_evaluator=_evaluate_claims,
        )
