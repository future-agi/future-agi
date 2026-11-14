"""Import admission limits, advertised sources and the derivative queue on PG.

Storage and the remote source are explicit doubles; the sandbox, budgets,
fences and the partial queue index are real. Every test only uses names that
also exist on the previous head, so a base-control run reaches its assertions.
"""
# Pytest injects the imported fixtures (env, rec) by their names.
# ruff: noqa: F811

import sys
from datetime import timedelta

import pytest
from django.db import connection
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
    contract,
    derivatives,
    imports,
    provider_recordings,
    uploads,
    wire,
)
from tracer.services.attachments.contract import AttachmentError
from tracer.tests.test_attachment_api import api  # noqa: F401  (HTTP fixture)
from tracer.tests.test_attachment_recording_imports import (  # noqa: F401
    Store,
    body,
    budgets,
    env,
    envelope,
    session,
    set_allow_import,
    wav_bytes,
)
from tracer.tests.test_attachment_recording_lifecycle import (  # noqa: F401
    imported,
    occurrence_of,
    rec,
    unlimited_retention,
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.unit,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="seccomp"),
]

IDLE = (0, 0, 0, 0, 0, 0)
WINDOW = 8 * 1024 * 1024  # ``imports.server_limits().window`` by default
HEADER = 44


class Counting:
    """A remote source double that counts every byte it hands out."""

    def __init__(self, data, window):
        self.data, self.window, self.fetched = data, window, 0

    def read_range(self, offset, length, sink):
        assert 0 < length <= self.window
        self.fetched += length
        sink.write(self.data[offset : offset + length])


def _project_byte_cap(monkeypatch, cap):
    deployment, organization, _ = imports.IMPORT_BUDGETS["bytes"]
    monkeypatch.setitem(
        imports.IMPORT_BUDGETS, "bytes", (deployment, organization, cap)
    )


def _submit(env, data):
    current = session(env)
    return imports.submit(
        env[2],
        scope_id=env[0].id,
        body=body(current, data=data, envelope_id=envelope(env, data)),
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
                    source_factory=lambda j, limits: source,
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
    # Admission's lower bound of the peak (source + window + segments >= source)
    # exceeds the project cap by one byte: nothing is reserved or fetched.
    _project_byte_cap(monkeypatch, 2 * len(data) + WINDOW - 1)
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
    # Passes admission's lower bound, but the exact plan (3 segment headers,
    # len(data) + 88 bytes of segments) is 38 bytes over the project cap.
    _project_byte_cap(monkeypatch, 2 * len(data) + WINDOW + 50)
    _, job = _submit(env, data)
    store, source = Store(), Counting(data, WINDOW)
    states = _run_until_terminal(job, store, source)
    assert (job.state, job.reason) == ("rejected", "too_large"), states
    assert source.fetched == len(data)  # one pass, never refetched
    assert job.attempts == 1 and job.released_at is not None
    assert not AttachmentRecordingSegment.objects.filter(
        recording_id=job.recording_id
    ).exists()
    assert not store.objects and _idle()
    assert imports.status(env[2], scope_id=env[0].id, job_id=job.id)["reason"] == (
        "too_large"
    )


def test_an_import_that_fits_exactly_still_publishes(env, monkeypatch):
    data = wav_bytes(seconds=65)
    _project_byte_cap(monkeypatch, 2 * len(data) + WINDOW + 2 * HEADER)
    _, job = _submit(env, data)
    store, source = Store(), Counting(data, WINDOW)
    assert _run_until_terminal(job, store, source) == ["ready"]
    assert source.fetched == len(data) and _idle()


# ----------------------------------------- decision 5: the global import row


def _global_row(**fields):
    AttachmentBudget.objects.update_or_create(
        key="global:imports",
        defaults={"window_started_at": timezone.now(), **fields},
    )


def test_the_global_import_row_defaults_to_half_the_upload_deployment_cap(env):
    transfers, deployment_bytes, _, _ = uploads.BUDGETS[0]
    data = wav_bytes(seconds=2)
    need = len(data) + WINDOW
    # Bytes: half the deployment cap, not all of it.
    _global_row(reserved_bytes=deployment_bytes // 2 - need + 1)
    with pytest.raises(AttachmentError, match="throttled"):
        _submit(env, data)
    _global_row(reserved_bytes=deployment_bytes // 2 - need)
    _submit(env, data)
    # Transfers: half the deployment's concurrent transfers.
    _global_row(reserved_bytes=0, active_transfers=transfers // 2)
    with pytest.raises(AttachmentError, match="throttled"):
        _submit(env, data)
    assert AttachmentImportJob.objects.count() == 1


def test_the_global_import_cap_is_configurable_and_fails_closed(env, settings):
    _, deployment_bytes, _, _ = uploads.BUDGETS[0]
    data = wav_bytes(seconds=2)
    settings.FI_ATTACHMENTS_IMPORT_GLOBAL_PERCENT = 25
    _global_row(reserved_bytes=deployment_bytes // 4 - WINDOW)
    with pytest.raises(AttachmentError, match="throttled"):
        _submit(env, data)
    for invalid in (0, 101, "50"):
        settings.FI_ATTACHMENTS_IMPORT_GLOBAL_PERCENT = invalid
        _global_row(reserved_bytes=0)
        with pytest.raises(AttachmentError, match="throttled"):
            _submit(env, data)
    assert not AttachmentImportJob.objects.exists()
    settings.FI_ATTACHMENTS_IMPORT_GLOBAL_PERCENT = 100
    _global_row(reserved_bytes=deployment_bytes // 2)
    _, job = _submit(env, data)
    assert job.state == "importing"


# ---------------------------------------------- decision 3: derivative queue


def _queue_plan():
    with CaptureQueriesContext(connection) as captured:
        derivatives.pending(limit=20)
    (sql,) = [
        query["sql"]
        for query in captured.captured_queries
        if "tracer_attachmentrecordingsegment" in query["sql"]
    ]
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute("EXPLAIN " + sql)
        plan = "\n".join(row[0] for row in cursor.fetchall())
        cursor.execute("SET LOCAL enable_seqscan = on")
    return plan


def test_the_derivative_queue_reads_its_partial_index(rec):
    imported(rec, seconds=10, channels=1, generate=False)
    plan = _queue_plan()
    print(f"\nQUEUE-PLAN\n{plan}")
    assert "attachment_derivative_due_idx" in plan, plan


def test_finished_and_dead_segments_leave_the_queue_and_stay(rec):
    _, (done,) = imported(rec, seconds=10, channels=1, generate=False)
    _, (dead,) = imported(rec, seconds=10, channels=1, generate=False)
    _, (lost,) = imported(rec, seconds=10, channels=1, generate=False)
    _, (open_,) = imported(rec, seconds=10, channels=1, generate=False)
    derivatives.generate(done.pk, scope_id=done.scope_id, storage=rec.store)
    derivatives.generate(lost.pk, scope_id=lost.scope_id, storage=rec.store)
    # ``generate`` stamps the segment it finished.
    done.refresh_from_db()
    assert done.derivatives_done_at is not None
    # A worker that died after publishing but before its stamp: purge finds it.
    AttachmentRecordingSegment.objects.filter(pk=lost.pk).update(
        derivatives_done_at=None
    )
    AttachmentOccurrence.objects.filter(pk=occurrence_of(dead).pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert [pk for pk, _ in derivatives.pending(limit=20)] == [open_.pk]
    assert derivatives.purge(limit=100) == 2  # lost + dead
    stamped = set(
        AttachmentRecordingSegment.objects.filter(
            derivatives_done_at__isnull=False
        ).values_list("pk", flat=True)
    )
    assert stamped == {done.pk, lost.pk, dead.pk}
    assert derivatives.purge(limit=100) == 0
    # Rows are kept, so the manifest of the dead recording still fails closed.
    assert AttachmentRecordingSegment.objects.filter(pk=dead.pk).exists()
    assert (
        derivatives.recording_manifest(
            scope_id=dead.scope_id,
            recording_id=AttachmentRecording.objects.get(
                pk=dead.recording_id
            ).client_id,
        )
        is None
    )
    # A disabled scope is not terminal: its open segment stays queued.
    # ``enabled`` is in the 0133 fence's policy tuple: bump the version with it.
    AttachmentScope.objects.filter(pk=rec.scope.pk).update(
        enabled=False, policy_version=F("policy_version") + 1
    )
    assert derivatives.purge(limit=100) == 0
    AttachmentScope.objects.filter(pk=rec.scope.pk).update(
        enabled=True, policy_version=F("policy_version") + 1
    )
    assert [pk for pk, _ in derivatives.pending(limit=20)] == [open_.pk]


def test_the_recording_sweep_purges_the_derivative_queue(rec, settings, monkeypatch):
    from tracer.tasks import attachment_recordings as tasks

    _, (dead,) = imported(rec, seconds=10, channels=1, generate=False)
    AttachmentOccurrence.objects.filter(pk=occurrence_of(dead).pk).update(
        deleted_at=timezone.now()
    )
    settings.FI_ATTACHMENTS_ENABLED = True
    monkeypatch.setattr(tasks, "_ready", lambda: None)
    tasks.sweep_attachment_recordings._original_func()
    dead.refresh_from_db()
    assert dead.derivatives_done_at is not None


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
