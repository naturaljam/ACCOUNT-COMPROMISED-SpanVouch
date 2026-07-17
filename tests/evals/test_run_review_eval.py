import json
from pathlib import Path

from afc.evals.review_metrics import ReviewEvaluationReport
from afc.evals.run_review_eval import main, write_report
from afc.review.models import canonical_json


def test_default_offline_cli_writes_byte_exact_canonical_one_line_json(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    assert main(["--output", str(first)]) == 0
    assert main(["--output", str(second)]) == 0

    content = first.read_bytes()
    assert content == second.read_bytes()
    assert content.endswith(b"\n")
    assert b"\r" not in content
    assert content.count(b"\n") == 1
    parsed = ReviewEvaluationReport.model_validate_json(content)
    assert content == (canonical_json(parsed) + "\n").encode("utf-8")


def test_default_cli_detects_all_injected_defects_without_external_usage(
    tmp_path: Path,
) -> None:
    output = tmp_path / "report.json"

    assert main(["--output", str(output)]) == 0

    report = ReviewEvaluationReport.model_validate_json(output.read_text(encoding="utf-8"))
    assert report.candidate_count == 36
    assert report.metrics.hard_defect_recall == 1.0
    assert report.metrics.unsupported_scope_detection_rate == 1.0
    assert report.metrics.operational_error_rate == 0.0
    assert report.usage.provider_sample_count == 0
    assert report.usage.total_tokens == 0


def test_write_report_excludes_gold_label_fields(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    assert main(["--output", str(output)]) == 0
    report = ReviewEvaluationReport.model_validate_json(output.read_text(encoding="utf-8"))

    rewritten = tmp_path / "rewritten.json"
    write_report(report, rewritten)
    document = json.loads(rewritten.read_text(encoding="utf-8"))

    assert rewritten.read_bytes() == output.read_bytes()
    assert all("expected_verdict" not in sample for sample in document["samples"])
    assert all("expected_finding_codes" not in sample for sample in document["samples"])
