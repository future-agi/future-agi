"""Import admission limits, advertised sources and the derivative queue on PG.

Storage and the remote source are explicit doubles; the sandbox, budgets,
fences and the partial queue index are real. Every test only uses names that
also exist on the previous head, so a base-control run reaches its assertions.
"""
# Pytest injects the imported fixtures (env, rec) by their names.
# ruff: noqa: F811

import sys
from dataclasses import replace
from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.db.models import F, deletion
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tracer.models.attachment import (
    AttachmentBudget,
    AttachmentOccurrence,
    AttachmentScope,
)
from tracer.models.attachment_recording import (
    AttachmentImportJob,
    AttachmentRecording,
    AttachmentRecordingSegment,
)
from tracer.services.attachments import (
    capacity,
    contract,
    control_plane,
    derivatives,
    imports,
    provider_recordings,
    retention,
    wire,
)
from tracer.services.attachments.contract import AttachmentError
from tracer.tests.test_attachment_api import api  # noqa: F401  (HTTP fixture)
from tracer.tests.test_attachment_recording_imports import (  # noqa: F401
    MemorySource,
    Store,
    body,
    budgets,
    cleanup_import_job,
    configure_recording_sources,
    env,
    envelope,
    reader_factory,
    routed_store,
    session,
    set_allow_import,
    submit_job,
    wav_bytes,
)
from tracer.tests.test_attachment_recording_lifecycle import (  # noqa: F401
    derivative_rows,
    imported,
    occurrence_of,
    rec,
    unlimited_retention,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.unit,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="seccomp"),
]

IDLE = (0, 0, 0, 0, 0, 0)
WINDOW = 8 * 1024 * 1024  # ``imports.server_limits().window`` by default
HEADER = 44


class Counting(MemorySource):
    """A remote source double that counts every byte it hands out."""

    def __init__(self, data, window):
        super().__init__(data, window)
        self.fetched = 0

    def read_range(self, offset, length, sink):
        assert 0 < length <= self.window
        self.fetched += length
        sink.write(self.data[offset : offset + length])


def _project_byte_cap(monkeypatch, cap):
    # Test-only smaller bound; every producer still reads the same fixed pool
    # and aggregate project vector, never a separate import quota dictionary.
    monkeypatch.setattr(
        capacity, "PROJECT_CAP", replace(capacity.PROJECT_CAP, bytes=cap)
    )


def _submit(env, data):
    current = session(env)
    return submit_job(
        env[2],
        scope_id=env[0].id,
        body=body(current, data=data, envelope_id=envelope(env, data, current=current)),
    )


def _run_until_terminal(job, store, source):
    states = []
    for _ in range(imports.MAX_ATTEMPTS + 1):
        states.append(
            str(
                imports.run(
                    job.id,
                    scope_id=job.scope_id,
                    storage=store,
                    source_factory=reader_factory(source),
                )
            )
        )
        if states[-1] != "importing":
            break
    job.refresh_from_db()
    return states


def _idle():
    return set(budgets().values()) <= {IDLE}


# ------------------------------------------------- decision 1: allowed_sources


def test_policy_advertises_no_import_source_nothing_deployed_can_create(
    rec, monkeypatch
):
    scope = rec.scope
    set_allow_import(scope, True)
    scope.refresh_from_db()
    monkeypatch.setattr(contract, "IMPORT_SOURCE_PRODUCERS", frozenset())
    document = contract.policy_document(scope)
    wire.validate("Policy", document)
    # Nothing deployed seals an envelope or registers a provider output, so
    # with imports on the policy still offers exactly the upload sources.
    assert document["allowed_sources"] == list(contract.UPLOAD_SOURCES)
    # A qualified reader alone does not make ProviderFile creatable either.
    monkeypatch.setitem(provider_recordings.READERS, "livekit", object())
    assert contract.policy_document(scope)["allowed_sources"] == list(
        contract.UPLOAD_SOURCES
    )


# --------------------------------------------- decision 2: oversized imports


def test_an_import_that_can_never_fit_is_refused_at_admission(env, monkeypatch):
    data = wav_bytes(seconds=65)  # 3 segments: they total len(data) + 2 * 44
    # Even the source + one-output-window floor exceeds the fixed project cap
    # by one byte: nothing is reserved or fetched.
    _project_byte_cap(monkeypatch, len(data) + WINDOW - 1)
    with pytest.raises(AttachmentError) as refused:
        _submit(env, data)
    assert (refused.value.code, refused.value.status) == ("too_large", 413)
    assert wire.public_error(refused.value) == (413, "too_large")
    assert not AttachmentImportJob.objects.exists()
    assert not AttachmentRecording.objects.filter(scope=env[0]).exists()
    assert _idle()


def test_an_import_whose_exact_plan_cannot_fit_is_rejected_after_one_fetch(
    env, monkeypatch
):
    data = wav_bytes(seconds=65)
    # Floor S+W fits. Exact peak is S+W+3*(S+88) for three WAV segments:
    # the three possible physical versions put it 214 bytes over this cap.
    _project_byte_cap(monkeypatch, 4 * len(data) + WINDOW + 50)
    _, job = _submit(env, data)
    store, source = Store(), Counting(data, WINDOW)
    states = _run_until_terminal(job, store, source)
    assert (job.state, job.reason) == ("rejected", "too_large"), states
    assert source.fetched == len(data)  # one pass, never refetched
    assert job.attempts == 1 and job.reservation_settled_at is None
    assert cleanup_import_job(job, store, monkeypatch) == {
        "expired": 0,
        "released": 1,
        "deferred": 0,
    }
    assert job.reservation_settled_at is not None
    assert not AttachmentRecordingSegment.objects.filter(
        recording_id=job.recording_id
    ).exists()
    assert not store.objects and _idle()
    assert imports.status(env[2], scope_id=env[0].id, job_id=job.id)["reason"] == (
        "too_large"
    )


def test_an_import_that_fits_exactly_still_publishes(env, monkeypatch):
    data = wav_bytes(seconds=65)
    _project_byte_cap(monkeypatch, 4 * len(data) + WINDOW + 6 * HEADER)
    _, job = _submit(env, data)
    store, source = Store(), Counting(data, WINDOW)
    assert _run_until_terminal(job, store, source) == ["ready"]
    # One bounded final-byte revalidation, not a second whole-source fetch.
    assert source.fetched == len(data) + 1 and not _idle()
    assert cleanup_import_job(job, store, monkeypatch) == {
        "expired": 0,
        "released": 1,
        "deferred": 0,
    }
    assert _idle()


# ----------------------------------------- decision 5: the global import row


def _global_row(**fields):
    AttachmentBudget.objects.update_or_create(
        key="global:imports",
        defaults={"window_started_at": timezone.now(), **fields},
    )


def test_the_global_import_row_defaults_to_half_the_upload_deployment_cap(env):
    """Historical case ID retained; the frozen split is now 3 GiB / 48 transfers."""
    assert capacity.IMPORT_POOL_CAP.bytes == 3_221_225_472
    assert capacity.IMPORT_POOL_CAP.transfers == 48
    data = wav_bytes(seconds=2)
    need = len(data) + WINDOW
    _global_row(reserved_bytes=3_221_225_472 - need + 1)
    with pytest.raises(AttachmentError, match="throttled"):
        _submit(env, data)
    _global_row(reserved_bytes=3_221_225_472 - need)
    _submit(env, data)
    _global_row(reserved_bytes=0, active_transfers=48)
    with pytest.raises(AttachmentError, match="throttled"):
        _submit(env, data)
    assert AttachmentImportJob.objects.count() == 1


def test_the_global_import_cap_is_configurable_and_fails_closed(env, settings):
    """Legacy per-process percentage must never enlarge the frozen pool."""
    data = wav_bytes(seconds=2)
    need = len(data) + WINDOW
    for obsolete in (25, 0, 101, "50", 100):
        settings.FI_ATTACHMENTS_IMPORT_GLOBAL_PERCENT = obsolete
        _global_row(reserved_bytes=3_221_225_472 - need + 1)
        with pytest.raises(AttachmentError, match="throttled"):
            _submit(env, data)
    assert not AttachmentImportJob.objects.exists()
    _global_row(reserved_bytes=3_221_225_472 - need)
    _, job = _submit(env, data)
    assert job.state == "importing"


# ---------------------------------------------- decision 3: derivative queue


def _queue_plan():
    with CaptureQueriesContext(connection) as captured:
        derivatives.pending(limit=20)
    (sql,) = [
        query["sql"]
        for query in captured.captured_queries
        if "tracer_attachmentrecordingderivative" in query["sql"]
    ]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute("EXPLAIN " + sql)
        plan = "\n".join(row[0] for row in cursor.fetchall())
        cursor.execute("SET LOCAL enable_seqscan = on")
    return plan


def test_the_derivative_queue_reads_its_partial_index(rec):
    imported(rec, seconds=10, channels=1, generate=False)
    plan = _queue_plan()
    print(f"\nQUEUE-PLAN\n{plan}")
    assert "att_derivative_dispatch_due" in plan, plan


def test_finished_and_dead_segments_leave_the_queue_and_stay(rec, monkeypatch):
    segments = [
        imported(rec, seconds=1, channels=1, generate=False)[1][0] for _ in range(4)
    ]
    done, dead, lost, open_ = [derivative_rows(segment).get() for segment in segments]
    assert control_plane.reconcile_tick()["bound"] == 4
    assert (
        derivatives.generate(done.id, scope_id=done.scope_id, storage=rec.store)
        == "ready"
    )
    # The receipt and immutable identity commit together. Losing the reply after
    # commit cannot return the finished representation to the production queue.
    record = derivatives._record

    def lost_ack(*args, **kwargs):
        assert record(*args, **kwargs) == "recorded"
        raise RuntimeError("lost acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(derivatives, "_record", lost_ack)
        with pytest.raises(RuntimeError, match="lost acknowledgement"):
            derivatives.generate(lost.id, scope_id=lost.scope_id, storage=rec.store)
    for row in (done, lost):
        row.refresh_from_db()
        assert row.state == "ready" and row.published_at is not None
        assert rec.store.identities[row.storage_key].version == row.storage_version
    AttachmentOccurrence.objects.filter(pk=occurrence_of(segments[1]).pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert retention.expire_occurrences(scope_id=rec.scope.id) == 1
    # Published rows never need production again, but each has a deadline to
    # clean up unowned versions. Pin only the clock used to read the queue;
    # generation, erasure and their database fences still use real time.
    deadlines = (done.unowned_cleanup_due_at, lost.unowned_cleanup_due_at)
    assert all(deadline is not None for deadline in deadlines)
    before_cleanup = min(deadlines) - timedelta(microseconds=1)
    cleanup_due = max(deadlines)

    def assert_pending_at(instant, *expected):
        with monkeypatch.context() as clock:
            clock.setattr(timezone, "now", lambda: instant)
            pending = derivatives.pending(limit=20)
        assert sorted(pending) == sorted((row.id, row.scope_id) for row in expected)

    assert_pending_at(before_cleanup, open_)
    assert derivatives.purge(limit=100) == 1
    dead.refresh_from_db()
    assert dead.state == "cleanup" and dead.published_at is None
    assert derivatives.purge(limit=100) == 0
    assert (
        derivatives.generate(dead.id, scope_id=dead.scope_id, storage=rec.store)
        == "settled"
    )
    dead.refresh_from_db()
    assert dead.state == "erased" and dead.storage_key not in rec.store.objects
    assert AttachmentRecordingSegment.objects.filter(pk=segments[1].pk).exists()
    from tracer.tests import test_attachment_api as http

    http.assert_error(
        rec.api.client.get(
            f"{http.base(rec.api)}/recordings/{segments[1].recording.client_id}/manifest",
            {"trace_id": http.TRACE, "span_id": http.SPAN},
            **http.auth(rec.api.token),
        ),
        404,
        "not_found",
    )
    # Disabling the scope hides production, not due cleanup of published rows.
    AttachmentScope.objects.filter(pk=rec.scope.pk).update(
        enabled=False, policy_version=F("policy_version") + 1
    )
    assert_pending_at(before_cleanup)
    assert_pending_at(cleanup_due, done, lost)
    assert derivatives.purge(limit=100) == 0
    AttachmentScope.objects.filter(pk=rec.scope.pk).update(
        enabled=True, policy_version=F("policy_version") + 1
    )
    assert_pending_at(before_cleanup, open_)
    assert_pending_at(cleanup_due, done, lost, open_)


def test_the_recording_sweep_purges_the_derivative_queue(rec, settings):
    from tracer.tasks import attachment_recordings as tasks

    _, (dead,) = imported(rec, seconds=10, channels=1, generate=False)
    AttachmentOccurrence.objects.filter(pk=occurrence_of(dead).pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert retention.expire_occurrences(scope_id=rec.scope.id) == 1
    settings.FI_ATTACHMENTS_ENABLED = True
    tasks.sweep_attachment_recordings._original_func()
    row = derivative_rows(dead).get()
    assert row.state == "cleanup" and row.published_at is None
    assert row.unowned_cleanup_due_at is not None


# --------------------------- review P3: foreign keys into accounts_user (0135)

ATTACHMENT_MODULES = {
    "tracer.models.attachment",
    "tracer.models.attachment_recording",
}


def test_every_attachment_foreign_key_to_users_is_known_and_never_protect():
    """Both halves of the proof must agree; the catalog alone cannot show it.

    Django enforces ``on_delete`` in Python and emits no ON DELETE clause, so
    PROTECT would also read ``confdeltype = a``. Every FK into
    ``accounts_user`` from an attachment table, read from ``pg_constraint``,
    must map to exactly one model field whose ``on_delete`` is never
    PROTECT/RESTRICT. SET_NULL also needs a nullable column, which the
    catalog does show.
    """
    from django.apps import apps

    user_table = apps.get_model("accounts", "User")._meta.db_table
    fields = {}
    for model in apps.get_app_config("tracer").get_models():
        if model.__module__ not in ATTACHMENT_MODULES:
            continue
        for field in model._meta.concrete_fields:
            if field.is_relation and field.related_model._meta.db_table == user_table:
                fields[(model._meta.db_table, field.column)] = field
    tables = sorted(
        {
            model._meta.db_table
            for model in apps.get_app_config("tracer").get_models()
            if model.__module__ in ATTACHMENT_MODULES
        }
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT cl.relname, a.attname, c.confdeltype, a.attnotnull
            FROM pg_constraint c
            JOIN pg_class cl ON cl.oid = c.conrelid
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.contype = 'f'
              AND c.confrelid = %s::regclass
              AND cl.relname = ANY (%s)
            ORDER BY 1, 2
            """,
            [user_table, tables],
        )
        catalog = {
            (table, column): (kind, notnull)
            for table, column, kind, notnull in cursor.fetchall()
        }
    assert set(catalog) == set(fields), (catalog, sorted(fields))
    assert ("tracer_attachmentproviderconnection", "created_by_id") in catalog
    for key, field in fields.items():
        on_delete = field.remote_field.on_delete
        assert on_delete not in (deletion.PROTECT, deletion.RESTRICT), key
        assert on_delete in (deletion.CASCADE, deletion.SET_NULL), key
        kind, notnull = catalog[key]
        assert kind == "a", key  # Django: NO ACTION in the DB, rule in Python
        if on_delete is deletion.SET_NULL:
            assert field.null and not notnull, key
