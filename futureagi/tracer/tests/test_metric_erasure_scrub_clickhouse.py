# ruff: noqa: F811
"""Finished erasures keep a count and a keyed digest, never the erased IDs.

Real PG + ClickHouse: a users erasure redacts a captured snapshot, completes,
and its parent and redaction child no longer hold any reversible identifier.
Failed and pending requests keep their IDs until they complete; requests that
finished before scrubbing existed are backfilled by the periodic sweep.
"""

import json
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db.models import Q
from django.utils import timezone

from tracer.models.derived_metric import (
    ERASURE_SUBJECT_DIGEST,
    MetricErasureRequest,
    MetricLedgerConflict,
    MetricProjectRefresh,
    MetricRefreshAttempt,
    MetricStateConflict,
    _sha256_json,
)
from tracer.services.derived_metrics import erasure, retention
from tracer.services.derived_metrics.deletion_protocol import _intent
from tracer.services.derived_metrics.lease import project_lease
from tracer.services.derived_metrics.refresh import RefreshWriter
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    field_query,
    live_writer,
    metric_ch,
    prepare_field,
    publication_store,
)
from tracer.tests.test_derived_metric_snapshot_erasure_clickhouse import (  # noqa: F401
    capture_case,
    execute,
)

pytestmark = pytest.mark.django_db(transaction=True)

ERASED = "erase-me"
STORES = set(MetricErasureRequest.Store.values)


def _request_rows(parent):
    """Every stored column of a request and its redaction children, as JSON."""

    rows = list(
        MetricErasureRequest.objects.filter(
            Q(id=parent.id) | Q(store_payload__parent=str(parent.id))
        ).values()
    )
    return rows, json.dumps(rows, default=str)


def _outbox_row(**fields):
    """Insert like deletion_protocol._intent: no save() signals or FK fill-in."""

    (row,) = MetricErasureRequest.objects.bulk_create([MetricErasureRequest(**fields)])
    return row


def _assert_scrubbed_parent(request, *, subjects, legacy_hash):
    assert request.status == "done" and set(request.receipts) == STORES
    assert request.subject_ids == []
    assert request.subject_count == len(subjects)
    scrub = request.store_payload["subject_scrub"]
    assert scrub["algorithm"] == ERASURE_SUBJECT_DIGEST
    assert len(scrub["salt"]) == 32
    assert scrub["subject_ids"]["count"] == len(subjects)
    digest = scrub["subject_ids"]["sha256"]
    assert len(digest) == 64 and request.subject_sha256 == digest
    # The plain record-time sha256 is reversible for short IDs: it is gone.
    assert request.subject_sha256 != legacy_hash
    # Auditable: whoever holds the server key and the erased set can verify it.
    assert MetricErasureRequest._subject_digest(subjects, scrub["salt"]) == digest


def _assert_scrubbed_child(child, *, parent):
    assert child.status == "done" and set(child.receipts) == STORES
    assert "subjects" not in child.store_payload
    assert child.subject_ids == []
    # Operational keys other queries rely on are preserved.
    assert child.store_payload["protocol"] == "metric_snapshot_redaction"
    assert child.store_payload["parent"] == str(parent.id)
    assert child.store_payload["subject_kind"] == "users"
    assert child.store_payload["removed_units"] == 1
    scrub = child.store_payload["subject_scrub"]
    assert scrub["algorithm"] == ERASURE_SUBJECT_DIGEST
    assert scrub["subjects"]["count"] >= 1
    assert len(scrub["subjects"]["sha256"]) == 64
    assert "subject_ids" not in scrub


def test_completed_erasure_scrubs_ids_to_count_and_digest(capture_case, live_writer):
    metric, run, _ = capture_case
    store = live_writer.store
    execute(run, store)
    assert run.status == "complete", run.error
    parent = erasure.erase_metric_data(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope_kind="users",
        subject_ids=[ERASED],
    )
    legacy_hash = parent.subject_sha256
    assert parent.subject_ids == [ERASED] and legacy_hash == _sha256_json([ERASED])

    assert erasure.process_erasure(parent.id, store=store, writer=live_writer)
    stale = MetricErasureRequest.objects.get(pk=parent.id)
    _assert_scrubbed_parent(stale, subjects=[ERASED], legacy_hash=legacy_hash)
    child = MetricErasureRequest.objects.get(
        deletion_key=f"redact:{parent.id}:{run.id}"
    )
    _assert_scrubbed_child(child, parent=parent)
    run.refresh_from_db()
    assert run.removed_unit_count == 1 and run.change_reason == "data_erasure"
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE user_id = %(u)s",
        {"u": ERASED},
    ) == [(0,)]

    rows, text = _request_rows(parent)
    assert len(rows) == 2 and all(row["status"] == "done" for row in rows)
    assert ERASED not in text and legacy_hash not in text

    # Idempotent: a re-run is a done no-op, the backfill finds nothing, and the
    # child's dedup key still suppresses a second redaction of the same run.
    assert erasure.process_erasure(parent.id, store=store, writer=live_writer)
    assert MetricErasureRequest.scrub_completed() == 0
    assert (
        MetricErasureRequest.objects.filter(
            store_payload__parent=str(parent.id)
        ).count()
        == 1
    )
    again = MetricErasureRequest.objects.get(pk=parent.id)
    assert again.store_payload == stale.store_payload
    assert again.subject_sha256 == stale.subject_sha256
    # A stale in-memory copy can never write the IDs back.
    with pytest.raises(MetricLedgerConflict):
        parent.save()
    assert MetricErasureRequest.objects.get(pk=parent.id).subject_ids == []

    # A request that has not completed keeps its IDs, through the backfill.
    pending = erasure.erase_metric_data(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope_kind="users",
        subject_ids=["keep-pending"],
    )
    assert MetricErasureRequest.scrub_completed() == 0
    pending.refresh_from_db()
    assert pending.status == "queued"
    assert pending.subject_ids == ["keep-pending"]
    assert pending.subject_sha256 == _sha256_json(["keep-pending"])
    assert "subject_scrub" not in pending.store_payload
    _, text = _request_rows(parent)
    assert ERASED not in text


def test_failed_erasure_keeps_ids_and_its_retry_completes_then_scrubs(
    capture_case, live_writer
):
    metric, run, _ = capture_case
    store = live_writer.store
    execute(run, store)
    assert run.status == "complete", run.error
    parent = erasure.erase_metric_data(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope_kind="users",
        subject_ids=[ERASED],
    )
    legacy_hash = parent.subject_sha256
    original = erasure.delete_rows

    def interrupt(*args):
        if args[1] == "derived_metric_inputs":
            raise RuntimeError("interrupted physical purge")
        return original(*args)

    with patch.object(erasure, "delete_rows", side_effect=interrupt):
        assert not erasure.process_erasure(parent.id, store=store, writer=live_writer)
    parent.refresh_from_db()
    child = MetricErasureRequest.objects.get(
        deletion_key=f"redact:{parent.id}:{run.id}"
    )
    # Failed requests still need their IDs for the retry.
    assert parent.status == "failed" and parent.receipts == {}
    assert parent.subject_ids == [ERASED] and parent.subject_sha256 == legacy_hash
    assert "subject_scrub" not in parent.store_payload
    assert child.status == "failed" and ERASED in child.store_payload["subjects"]
    assert "subject_scrub" not in child.store_payload
    # The backfill never touches unfinished work.
    assert MetricErasureRequest.scrub_completed() == 0
    child.refresh_from_db()
    assert ERASED in child.store_payload["subjects"]
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE user_id = %(u)s",
        {"u": ERASED},
    )[0][0]

    # The retry resumes from the kept IDs (child first, then the parent).
    for _ in range(3):
        if erasure.process_erasure(parent.id, store=store, writer=live_writer):
            break
    parent.refresh_from_db()
    child.refresh_from_db()
    assert parent.attempt_count >= 2
    _assert_scrubbed_parent(parent, subjects=[ERASED], legacy_hash=legacy_hash)
    _assert_scrubbed_child(child, parent=parent)
    assert all(set(counts) == {"remaining"} for counts in parent.receipts.values()), (
        parent.receipts
    )
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE user_id = %(u)s",
        {"u": ERASED},
    ) == [(0,)]
    _, text = _request_rows(parent)
    assert ERASED not in text and legacy_hash not in text
    # Completed and scrubbed stays completed: a later pass is a no-op success.
    assert erasure.process_erasure(parent.id, store=store, writer=live_writer)


def test_model_finish_scrubs_only_on_completion(workspace, observe_project):
    ids = [f"end-user-{uuid4()}" for _ in range(3)]
    request = MetricErasureRequest.record(
        organization_id=workspace.organization_id,
        project_id=observe_project.id,
        scope_kind="users",
        subject_ids=ids,
    )
    token = MetricErasureRequest.claim(request.id, ttl=timedelta(minutes=5))
    MetricErasureRequest.record_receipt(
        request.id, token=token, store="live_metric_rows", counts={"rows": 4}
    )
    # A missing receipt refuses completion and scrubs nothing.
    with pytest.raises(MetricStateConflict):
        MetricErasureRequest.finish(request.id, token=token)
    request.refresh_from_db()
    assert request.status == "running" and request.subject_ids == ids
    for name in ("snapshot_inputs", "snapshot_results"):
        MetricErasureRequest.record_receipt(
            request.id, token=token, store=name, counts={"rows": 0}
        )
    # A wrong claim refuses completion and scrubs nothing.
    with pytest.raises(MetricStateConflict):
        MetricErasureRequest.finish(request.id, token=uuid4())
    request.refresh_from_db()
    assert request.subject_ids == ids
    MetricErasureRequest.finish(request.id, token=token)
    request.refresh_from_db()
    assert request.receipts["live_metric_rows"] == {"rows": 4}
    _assert_scrubbed_parent(
        request, subjects=ids, legacy_hash=_sha256_json(sorted(ids))
    )
    for value in ids:
        assert value not in json.dumps(
            MetricErasureRequest.objects.filter(pk=request.id).values().get(),
            default=str,
        )


def test_sweep_backfills_requests_completed_before_scrubbing(
    workspace, observe_project
):
    org, project = workspace.organization_id, observe_project.id

    def users(subject):
        return MetricErasureRequest.record(
            organization_id=org,
            project_id=project,
            scope_kind="users",
            subject_ids=[subject],
        )

    now = timezone.now()
    legacy_parent = users("legacy-done")
    MetricErasureRequest.objects.filter(pk=legacy_parent.id).update(
        status="done",
        completed_at=now,
        receipts={name: {"remaining": 0} for name in STORES},
    )
    legacy_child = _outbox_row(
        organization_key=org,
        project_key=project,
        scope_kind="run",
        run_key=uuid4(),
        deletion_key=f"redact:{legacy_parent.id}:{uuid4()}",
        store_payload={
            "protocol": "metric_snapshot_redaction",
            "parent": str(legacy_parent.id),
            "subject_kind": "users",
            "subjects": ["legacy-done", "legacy-alias"],
            "removed_units": 2,
        },
        status="done",
        completed_at=now,
    )
    # An organization parent's child carries an EMPTY subject list. It must be
    # scrubbed once and then leave the candidate window for good.
    empty_child = _outbox_row(
        organization_key=org,
        project_key=project,
        scope_kind="run",
        run_key=uuid4(),
        deletion_key=f"redact:{uuid4()}:{uuid4()}",
        store_payload={
            "protocol": "metric_snapshot_redaction",
            "subject_kind": "organization",
            "subjects": [],
            "removed_units": 0,
        },
        status="done",
        completed_at=now,
    )
    queued = users("still-queued")
    failed = users("still-failed")
    token = MetricErasureRequest.claim(failed.id, ttl=timedelta(minutes=5))
    MetricErasureRequest.fail(failed.id, token=token, error_code="snapshot_down")
    running = users("still-running")
    MetricErasureRequest.claim(running.id, ttl=timedelta(minutes=5))
    unfinished = {
        row.id: (row.subject_ids, row.subject_sha256)
        for row in MetricErasureRequest.objects.filter(
            id__in=[queued.id, failed.id, running.id]
        )
    }

    # An OLDER done request with nothing identifying: if the candidate filter
    # matched it, it would take the whole limit=1 window and scrub nothing.
    clean = _outbox_row(
        organization_key=org,
        scope_kind="organization",
        status="done",
        completed_at=now - timedelta(days=1),
        receipts={name: {"remaining": 0} for name in STORES},
    )
    clean_before = MetricErasureRequest.objects.filter(pk=clean.id).values().get()
    assert MetricErasureRequest.scrub_completed(limit=1) == 1

    with patch.object(erasure, "process_erasure", return_value=False):
        erasure.sweep(limit=10)
    assert MetricErasureRequest.objects.filter(pk=clean.id).values().get() == (
        clean_before
    )

    legacy_parent.refresh_from_db()
    _assert_scrubbed_parent(
        legacy_parent,
        subjects=["legacy-done"],
        legacy_hash=_sha256_json(["legacy-done"]),
    )
    legacy_child.refresh_from_db()
    assert "subjects" not in legacy_child.store_payload
    assert legacy_child.store_payload["subject_scrub"]["subjects"]["count"] == 2
    assert legacy_child.store_payload["removed_units"] == 2
    empty_child.refresh_from_db()
    assert "subjects" not in empty_child.store_payload
    assert empty_child.store_payload["subject_scrub"]["subjects"]["count"] == 0
    for row in MetricErasureRequest.objects.filter(id__in=unfinished):
        assert (row.subject_ids, row.subject_sha256) == unfinished[row.id]
        assert "subject_scrub" not in row.store_payload
    done_text = json.dumps(
        list(MetricErasureRequest.objects.filter(status="done").values()),
        default=str,
    )
    assert "legacy-done" not in done_text and "legacy-alias" not in done_text

    # Idempotent and bounded: nothing left to scrub, and scrubbed rows never
    # come back as candidates.
    before = {
        row["id"]: row
        for row in MetricErasureRequest.objects.filter(
            Q(id=legacy_parent.id) | Q(id=legacy_child.id) | Q(id=empty_child.id)
        ).values()
    }
    assert MetricErasureRequest.scrub_completed(limit=1) == 0
    assert MetricErasureRequest.scrub_completed() == 0
    for row in MetricErasureRequest.objects.filter(id__in=before).values():
        assert row == before[row["id"]]
    with pytest.raises(ValueError):
        MetricErasureRequest.scrub_completed(limit=0)


@pytest.mark.parametrize("scope", ["project", "organization", "version", "history"])
def test_late_live_insert_acknowledged_during_erasure_is_scrubbed(
    live_writer, monkeypatch, scope
):
    writer = live_writer
    org, project, metric = writer.org_id, writer.lease.project_id, uuid4()
    stale = writer.begin()
    submission = _intent(
        organization_id=org,
        project_id=project,
        scope="history",
        store_payload={
            "protocol": "metric_submission",
            "attempt": str(stale.pk),
            "generation": stale.generation,
            "shard": writer.store.identity(),
        },
    )
    # Version deletion and history collection fence the attempt before the
    # worker sees its intent, without cancelling a previously registered send.
    if scope in {"version", "history"}:
        MetricRefreshAttempt.abort(
            stale.pk, reason="generation_revoked", **writer.lease.check()
        )
    request = _intent(
        organization_id=org,
        project_id=project if scope != "organization" else None,
        scope=scope,
        metric_key=metric if scope == "version" else None,
        version_key=uuid4() if scope == "version" else None,
        store_payload={"attempts": [{"id": str(stale.pk)}], "version": 1},
    )
    # The in-flight sender lands and ACKs AFTER this table's first DELETE. A
    # status check at pass end would report success with its row still present.
    params = {
        "org": str(org),
        "project": str(project),
        "metric": str(metric),
        "version": 1,
        "attempt": str(stale.pk),
        "generation": stale.generation,
    }
    insert = (
        "INSERT INTO metric_field_values_checkpoint "
        "(org_id, project_id, field_id, field_version, attempt_id, generation, unit_id) "
        "VALUES (%(org)s, %(project)s, %(metric)s, %(version)s, %(attempt)s, %(generation)s, 'late')"
    )
    control = {
        "project": {"project": str(uuid4())},
        "organization": {"org": str(uuid4())},
        "version": {"version": 2},
        "history": {"attempt": str(uuid4())},
    }[scope]
    writer.store.execute(insert, {**params, **control})
    delete = retention._delete
    landed = []

    def late(store, table, predicate, bound):
        delete(store, table, predicate, bound)
        if table.name == "metric_field_values_checkpoint" and not landed:
            store.execute(insert, params)
            MetricErasureRequest.objects.filter(pk=submission.pk).update(
                status="done",
                completed_at=timezone.now(),
                receipts={"synchronous_response": {"drained": 1}},
            )
            landed.append(True)

    monkeypatch.setattr(retention, "_delete", late)
    assert erasure.process_erasure(request.pk, store=writer.store)
    assert landed
    kept = {**params, **control}
    assert writer.store.execute(
        "SELECT toString(org_id), toString(project_id), field_version, toString(attempt_id) "
        "FROM metric_field_values_checkpoint WHERE unit_id = 'late'"
    ) == [(kept["org"], kept["project"], kept["version"], kept["attempt"])]


def test_subject_erasure_retries_physical_scrub_without_republishing(
    live_writer, monkeypatch
):
    writer = live_writer
    project = writer.lease.project_id
    keep, _ = prepare_field(writer, unit_id="keep", user="keep")
    writer.stage_and_publish(keep)
    erase, _ = prepare_field(writer, unit_id="private", user=ERASED)
    writer.stage_and_publish(erase)
    # A done flag alone is not proof the sender finished on this shard.
    submission = MetricErasureRequest.objects.get(
        store_payload__protocol="metric_submission",
        store_payload__attempt=str(erase.pk),
    )
    MetricErasureRequest.objects.filter(pk=submission.pk).update(receipts={})
    request = erasure.erase_metric_data(
        organization_id=writer.org_id,
        project_id=project,
        scope_kind="users",
        subject_ids=[ERASED],
    )
    assert not erasure.process_erasure(request.pk, store=writer.store, writer=writer)
    request.refresh_from_db()
    assert not request.receipts and not request.completed_at
    head = MetricProjectRefresh.for_project(project).head_manifest
    assert head.published_by.source_ceilings["erasure"] == str(request.pk)
    # A late private row even in a current-facts store (no field_id). Skipping
    # its mutation must fail verification rather than issue a zero receipt.
    writer.store.execute(
        "INSERT INTO list_current_user_activity (org_id, project_id, attempt_id, generation, canonical_user_id) "
        "VALUES (%(org)s, %(project)s, %(attempt)s, %(generation)s, %(user)s)",
        {
            "org": str(writer.org_id),
            "project": str(project),
            "attempt": str(erase.pk),
            "generation": erase.generation,
            "user": ERASED,
        },
    )
    MetricErasureRequest.objects.filter(pk=submission.pk).update(
        receipts={"synchronous_response": {"drained": 1}}
    )
    delete = retention._delete

    def skip_one(store, table, predicate, params):
        if table.name != "list_current_user_activity":
            delete(store, table, predicate, params)

    with monkeypatch.context() as patch:
        patch.setattr(retention, "_delete", skip_one)
        assert not erasure.process_erasure(request.pk, store=writer.store, writer=writer)
    request.refresh_from_db()
    assert not request.receipts and not request.completed_at
    assert erasure.process_erasure(request.pk, store=writer.store, writer=writer)
    assert MetricProjectRefresh.for_project(project).head_manifest_id == head.pk
    assert not writer.store.execute(
        "SELECT canonical_user_id FROM list_current_user_activity"
    )
    assert writer.store.execute(
        "SELECT unit_id FROM metric_field_values_checkpoint"
    ) == [("keep",)]


@pytest.mark.parametrize("scope", ["metric", "version"])
def test_definition_erasure_fences_compaction_only_until_scrub(
    live_writer, monkeypatch, scope
):
    writer, metric = live_writer, uuid4()
    first, _ = prepare_field(writer, metric=metric, unit_id="erase")
    writer.stage_and_publish(first)
    keep, _ = prepare_field(writer, unit_id="keep")
    writer.stage_and_publish(keep)
    # A detached deletion intent outlives its definition. The committed head
    # still contains the target and compaction would copy it into a new attempt.
    request = _intent(
        organization_id=writer.org_id,
        project_id=writer.lease.project_id,
        scope=scope,
        metric_key=metric,
        version_key=uuid4() if scope == "version" else None,
        store_payload={"version": 1} if scope == "version" else {},
    )
    old_copy = writer.begin(purpose="compaction")
    head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    writer.freeze_current(old_copy, retention.FIELD_VALUES, head)
    delete = retention._delete
    checked = []

    def during_scrub(store, table, predicate, params):
        if not checked:
            with project_lease(writer.lease.project_id, holder="during-scrub") as lease:
                with pytest.raises(MetricStateConflict, match="erasure"):
                    RefreshWriter(lease, store, org_id=writer.org_id).compact(server_side=False)
            checked.append(True)
        delete(store, table, predicate, params)

    with monkeypatch.context() as patch:
        patch.setattr(retention, "_delete", during_scrub)
        assert erasure.process_erasure(request.pk, store=writer.store)
    assert checked
    old_copy.refresh_from_db()
    assert old_copy.state == "aborted" and not old_copy.ledger_chunks.exists()
    with project_lease(writer.lease.project_id, holder="after-scrub") as lease:
        RefreshWriter(lease, writer.store, org_id=writer.org_id).compact(server_side=False)
    assert writer.store.execute(
        "SELECT unit_id FROM metric_field_values_checkpoint"
    ) == [("keep",)]
