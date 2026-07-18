from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from spanvouch.adapters.storage.sqlite import SQLiteReviewRepository
from spanvouch.contracts.diagnosis import DiagnoserKind
from spanvouch.contracts.review import ReviewStatus
from spanvouch.contracts.verification import VerificationMode
from spanvouch.contracts.versioning import canonical_json
from spanvouch.review.commands import CreateReviewCase, WorkflowEventType
from tests.review.factories import NOW, make_review_snapshot, make_revision


def _create_command(worker_id: str) -> CreateReviewCase:
    case_id = f"process-case-{worker_id}"
    initial_revision = make_revision().model_copy(
        update={
            "case_id": case_id,
            "revision_id": f"process-revision-{worker_id}",
        }
    )
    return CreateReviewCase(
        case_id=case_id,
        snapshot=make_review_snapshot(),
        initial_revision=initial_revision,
        target_status=ReviewStatus.PENDING_VERIFICATION,
        verification_mode=VerificationMode.DETERMINISTIC,
        diagnoser=DiagnoserKind.RULES,
        idempotency_scope=f"process-stability:{case_id}",
        idempotency_key=f"process-create-{worker_id}",
        request_sha256="a" * 64,
        event_id=f"process-created-{worker_id}",
        event_type=WorkflowEventType.CASE_CREATED,
        event_metadata_json=canonical_json({"worker_id": worker_id}),
        created_at=NOW,
    )


async def _run(database: Path, worker_id: str) -> dict[str, object]:
    repository = SQLiteReviewRepository(database)
    await repository.initialize()
    await repository.create_case(_create_command(worker_id))

    reopened = SQLiteReviewRepository(database)
    await reopened.initialize()
    detail = await reopened.get_detail(f"process-case-{worker_id}")
    return {
        "case_id": detail.case.case_id,
        "event_count": len(detail.events),
        "revision_count": len(detail.revisions),
        "status": detail.case.status.value,
        "verifier_report_count": len(detail.verifier_reports),
        "version": detail.case.version,
        "worker_id": worker_id,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--worker-id",
        choices=tuple(f"{index:02d}" for index in range(20)),
        required=True,
    )
    arguments = parser.parse_args()
    payload = asyncio.run(_run(arguments.database, arguments.worker_id))
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
