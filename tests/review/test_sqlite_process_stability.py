from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from afc.review.sqlite_repository import SQLiteReviewRepository

ROOT = Path(__file__).resolve().parents[2]
PROCESS_COUNT = 20
WORKER_MODULE = "tests.review.sqlite_process_probe"


def _collect_process_result(
    worker_id: str, process: subprocess.Popen[str]
) -> dict[str, object]:
    try:
        stdout, stderr = process.communicate(timeout=60)
        timed_out = False
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        timed_out = True
    return {
        "worker_id": worker_id,
        "returncode": process.returncode,
        "timed_out": timed_out,
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
    }


async def _audit_durable_cases(
    database: Path, case_ids: tuple[str, ...]
) -> tuple[dict[str, object], ...]:
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    audited: list[dict[str, object]] = []
    for case_id in case_ids:
        detail = await repository.get_detail(case_id)
        audited.append(
            {
                "case_id": detail.case.case_id,
                "event_count": len(detail.events),
                "revision_count": len(detail.revisions),
                "status": detail.case.status.value,
                "verifier_report_count": len(detail.verifier_reports),
                "version": detail.case.version,
            }
        )
    return tuple(audited)


def test_sqlite_repository_is_stable_across_twenty_independent_processes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "twenty-process-stability.sqlite3"
    worker_ids = tuple(f"{index:02d}" for index in range(PROCESS_COUNT))
    worker_environment = os.environ.copy()
    worker_environment.pop("DEEPSEEK_API_KEY", None)

    processes = tuple(
        (
            worker_id,
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    WORKER_MODULE,
                    "--database",
                    str(database),
                    "--worker-id",
                    worker_id,
                ],
                cwd=ROOT,
                env=worker_environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            ),
        )
        for worker_id in worker_ids
    )
    assert len({process.pid for _, process in processes}) == PROCESS_COUNT

    results = tuple(
        _collect_process_result(worker_id, process)
        for worker_id, process in processes
    )
    failures = tuple(
        result
        for result in results
        if result["timed_out"] or result["returncode"] != 0
    )
    assert not failures, json.dumps(failures, indent=2, sort_keys=True)

    payloads = tuple(json.loads(str(result["stdout"])) for result in results)
    expected_payloads = tuple(
        {
            "case_id": f"process-case-{worker_id}",
            "event_count": 1,
            "revision_count": 1,
            "status": "pending_verification",
            "verifier_report_count": 0,
            "version": 0,
            "worker_id": worker_id,
        }
        for worker_id in worker_ids
    )
    assert payloads == expected_payloads

    case_ids = tuple(str(payload["case_id"]) for payload in payloads)
    assert len(set(case_ids)) == PROCESS_COUNT
    audited = asyncio.run(_audit_durable_cases(database, case_ids))
    assert audited == tuple(
        {key: value for key, value in payload.items() if key != "worker_id"}
        for payload in expected_payloads
    )
