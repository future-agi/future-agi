"""Two-store feature publication: CH readback before PostgreSQL visibility."""

import hashlib
import json
import secrets
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from tracer.models.trace_grouping import (
    GroupingFeatureState,
    TraceGroupingFeature,
    TraceGroupingFeatureJob,
)
from tracer.queries.grouping import (
    canonical_grouping_source_digest,
    export_grouping_snapshot,
)
from tracer.services.grouping.control import (
    GroupingConflict,
    GroupingControlError,
    GroupingNotFound,
    _token_hash,
    mark_feature_ready,
    require_feature_job,
)
from tracer.services.grouping.feature_store import (
    GroupingFeatureStore,
    validate_feature_rows,
)


def _text_digest(text: str) -> str:
    return hashlib.sha256(
        json.dumps(text, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _view_texts(snapshot: dict) -> dict[tuple[str, str], str]:
    checks = {
        item["requirement_id"]: item["requirement"]
        for item in snapshot["report"]["requirement_checks"]
    }
    fallback_task = "\n".join(
        item["requirement"] for item in snapshot["report"]["requirement_checks"]
    )
    return {
        (occurrence["occurrence_id"], view): text
        for occurrence, finding in zip(
            snapshot["occurrences"], snapshot["report"]["findings"], strict=True
        )
        for view, text in (
            ("semantics", finding["statement"]),
            ("task", checks.get(finding["requirement_id"], fallback_task)),
        )
        if text
    }


def complete_feature_job(
    *,
    feature_job_id: uuid.UUID,
    lease_token: str,
    status: str,
    features: list[dict],
    error_code: str = "",
    store: GroupingFeatureStore | None = None,
) -> dict:
    if status not in {"ready", "failed"} or not isinstance(features, list):
        raise GroupingControlError("invalid feature completion")
    if not isinstance(error_code, str) or len(error_code) > 100:
        raise GroupingControlError("invalid feature error code")
    # The first transaction reads the source fence but does not hold PG locks
    # across ClickHouse I/O or MiniLM work.
    with transaction.atomic():
        job = (
            TraceGroupingFeatureJob.no_workspace_objects.select_for_update(of=("self",))
            .select_related("report__job")
            .filter(pk=feature_job_id)
            .first()
        )
        if job is None or not secrets.compare_digest(
            job.lease_token_digest, _token_hash(lease_token)
        ):
            raise GroupingNotFound("feature attempt was not found")
        if job.state == GroupingFeatureState.READY:
            if status == "ready":
                return {
                    "state": "ready",
                    "feature_digest": job.feature_digest,
                    "idempotent": True,
                }
            raise GroupingConflict("ready feature job cannot become failed")
        job = require_feature_job(
            feature_job_id=feature_job_id, lease_token=lease_token
        )
        snapshot = export_grouping_snapshot(report=job.report)
    if status == "failed":
        if features:
            raise GroupingControlError("failed feature completion cannot carry vectors")
        with transaction.atomic():
            job = require_feature_job(
                feature_job_id=feature_job_id, lease_token=lease_token
            )
            job.failure_code = error_code or "worker_failed"
            if job.attempt_number >= 5:
                job.state = GroupingFeatureState.FAILED
            else:
                job.state = GroupingFeatureState.PENDING
                job.not_before = timezone.now() + timedelta(
                    seconds=min(300, 2**job.attempt_number)
                )
            job.save(
                update_fields=["state", "not_before", "failure_code", "updated_at"]
            )
            return {"state": job.state, "failure_code": job.failure_code}
    occurrence_ids = {item["occurrence_id"] for item in snapshot["occurrences"]}
    try:
        validate_feature_rows(rows=features, occurrence_ids=occurrence_ids)
    except ValueError as exc:
        raise GroupingControlError(str(exc)) from exc
    expected_texts = _view_texts(snapshot)
    if {(row["occurrence_id"], row["view"]) for row in features} != set(expected_texts):
        raise GroupingControlError("feature views do not match normalized source")
    for row in features:
        if (
            row["source_digest"] != canonical_grouping_source_digest(snapshot)
            or row["evidence_revision"] != snapshot["report"]["evidence_digest"]
            or row["text_digest"]
            != _text_digest(expected_texts[row["occurrence_id"], row["view"]])
        ):
            raise GroupingConflict(
                "feature row is not bound to current normalized source"
            )
    releases = {row["serving_release"] for row in features}
    if len(releases) != 1:
        raise GroupingControlError("mixed serving releases are not one feature job")
    feature_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                sorted(
                    (row["occurrence_id"], row["view"], row["feature_digest"])
                    for row in features
                ),
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    (store or GroupingFeatureStore()).write_and_verify(
        organization_id=job.report.organization_id,
        project_id=job.report.project_id,
        rows=features,
    )
    with transaction.atomic():
        job = require_feature_job(
            feature_job_id=feature_job_id, lease_token=lease_token
        )
        if (
            export_grouping_snapshot(report=job.report)["snapshot_digest"]
            != snapshot["snapshot_digest"]
        ):
            raise GroupingConflict("report changed while feature index was written")
        for row in features:
            TraceGroupingFeature.no_workspace_objects.update_or_create(
                job=job,
                finding_id=uuid.UUID(row["occurrence_id"]),
                view=row["view"],
                defaults={
                    "source_digest": row["source_digest"],
                    "evidence_revision": row["evidence_revision"],
                    "text_digest": row["text_digest"],
                    "feature_digest": row["feature_digest"],
                    "model": row["model"],
                    "model_revision": None,
                    "serving_release": row["serving_release"],
                    "dimension": row["dimension"],
                    "bucket_keys": row["index_buckets"],
                },
            )
        return {
            **mark_feature_ready(
                job=job,
                feature_digest=feature_digest,
                serving_release=next(iter(releases)),
            ),
            "feature_digest": feature_digest,
            "idempotent": False,
        }
