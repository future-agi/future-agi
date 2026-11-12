"""Attachment control-plane load test: capture_session FK contention, fence cost.

Opt-in (``ATTACHMENT_LOAD=1``); ``tests/stress`` is excluded from the pytest
shards. Real PostgreSQL with the real 0132/0133 schema and the real service
code; object storage is the in-memory ``ImmutableStore`` double, so this
measures the database control plane only, not S3 or the parser sandbox.

Two load shapes, each under ONE capture session that is renewed through the
real ``bootstrap()`` every ``ATTACHMENT_LOAD_RENEW_SECONDS`` and revoked by a
plain UPDATE at 75 % of the run (there is no service writer of
``CaptureSession.revoked_at`` yet, so the revocation is simulated):

- ``service``: ``uploads.initialize`` -> ``transfer`` -> ``complete``, the
  shipped write path. It serialises on the AttachmentBudget rows ('global',
  org, project) and on ``AttachmentScope FOR UPDATE``.
- ``raw``: the same Attachment + Occurrence + Upload inserts and the
  upload/occurrence updates through the ORM with no budget or scope lock. Only
  here can the FK ``KEY SHARE`` locks on the one capturesession row overlap.

Each shape runs with the 0133 fences on and off (disabled by trigger name, so
the FK triggers stay on), and ``raw`` also with the capture_session FKs
dropped. A sampler records pg_stat_activity wait events and ungranted
pg_locks; MultiXact use comes from pg_control_checkpoint() and pg_stat_slru.
A second test seeds a 100k-row backlog and runs EXPLAIN (ANALYZE, BUFFERS) on
the expiry sweeps, the outbox drain, the orphan sweep and #3941's binding
claim. Results go to ``ATTACHMENT_LOAD_OUT`` (JSON + text).
"""

import json
import os
import random
import statistics
import threading
import time
import uuid
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import connection, connections, transaction
from django.db.models import F, Q
from django.utils import timezone

from model_hub.models.ai_model import AIModel
from tfc.middleware.workspace_context import set_workspace_context
from tracer.models.attachment import (
    Attachment,
    AttachmentOccurrence,
    AttachmentOutbox,
    AttachmentScope,
    AttachmentUpload,
    CaptureSession,
    UploadReservation,
)
from tracer.models.project import Project
from tracer.services.attachments import bootstrap as broker
from tracer.services.attachments import uploads
from tracer.services.attachments.contract import AttachmentError
from tracer.tests.test_attachment_core import ImmutableStore, wav_bytes

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("ATTACHMENT_LOAD") != "1",
        reason="opt-in load test: set ATTACHMENT_LOAD=1 (see module docstring)",
    ),
]

WRITERS = [
    int(n) for n in os.environ.get("ATTACHMENT_LOAD_WRITERS", "64,128,256").split(",")
]
SECONDS = float(os.environ.get("ATTACHMENT_LOAD_SECONDS", "20"))
RENEW_SECONDS = float(os.environ.get("ATTACHMENT_LOAD_RENEW_SECONDS", "1"))
BACKLOG = int(os.environ.get("ATTACHMENT_LOAD_BACKLOG", "100000"))
OUT = Path(os.environ.get("ATTACHMENT_LOAD_OUT", "/tmp/attachment-load"))
SAMPLE_SECONDS = 0.025
TRACE = "00112233445566778899aabbccddeeff"
WAV = wav_bytes()
FENCED = (
    "attachment",
    "attachmentblob",
    "attachmentoccurrence",
    "attachmentupload",
    "capturesession",
    "uploadreservation",
    "attachmentoutbox",
    "attachmentwritegrant",
)
# Operational safety bounds lifted so the run measures the database, not the
# 429 admission budget (which would otherwise cap a project at 120 init/min).
UNBOUNDED = ((10**9, 10**18, 10**9, 10**9),) * 3


def sql(query, params=None, *, fetch=True):
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall() if fetch and cursor.description else None


def set_fences(on):
    verb = "ENABLE" if on else "DISABLE"
    for table in FENCED:
        sql(
            f"ALTER TABLE tracer_{table} {verb} TRIGGER attachment_record_fence",
            fetch=False,
        )
    sql(
        f"ALTER TABLE tracer_attachmentscope {verb} TRIGGER attachment_scope_fence",
        fetch=False,
    )


def session_fks():
    return sql(
        """SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid)
           FROM pg_constraint c WHERE c.contype = 'f'
           AND c.confrelid = 'tracer_capturesession'::regclass ORDER BY 1"""
    )


def drop_fks(fks):
    for table, name, _ in fks:
        sql(f'ALTER TABLE {table} DROP CONSTRAINT "{name}"', fetch=False)


def restore_fks(fks):
    for table, name, definition in fks:
        sql(f'ALTER TABLE {table} ADD CONSTRAINT "{name}" {definition}', fetch=False)


def reset_rows():
    # TRUNCATE fires no row triggers; CASCADE reaches every attachment child.
    sql(
        "TRUNCATE tracer_capturesession, tracer_attachment, tracer_attachmentbudget CASCADE",
        fetch=False,
    )
    sql("VACUUM ANALYZE tracer_attachmentscope", fetch=False)


def multixact():
    sql("CHECKPOINT", fetch=False)
    ((mxid, offset),) = sql(
        "SELECT next_multixact_id::text::bigint, next_multi_offset::text::bigint "
        "FROM pg_control_checkpoint()"
    )
    slru = {
        name: {"zeroed": z, "written": w, "read": r, "hit": h}
        for name, z, w, r, h in sql(
            "SELECT name, blks_zeroed, blks_written, blks_read, blks_hit "
            "FROM pg_stat_slru WHERE name LIKE 'MultiXact%'"
        )
    }
    return {"next_multixact_id": mxid, "next_multi_offset": offset, "slru": slru}


def pct(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(p / 100 * len(ordered)))] * 1000, 2)


def summary(values):
    return {
        "n": len(values),
        "p50_ms": pct(values, 50),
        "p95_ms": pct(values, 95),
        "p99_ms": pct(values, 99),
        "max_ms": round(max(values) * 1000, 2) if values else None,
        "mean_ms": round(statistics.fmean(values) * 1000, 2) if values else None,
    }


class Sampler(threading.Thread):
    """Wait events of active backends and ungranted locks, every 25 ms."""

    def __init__(self, stop):
        super().__init__(daemon=True)
        self.stop = stop
        self.samples = 0
        self.active = 0
        self.waits = Counter()
        self.ungranted = Counter()
        self.max_ungranted = 0

    def run(self):
        try:
            with connections["default"].cursor() as cursor:
                while not self.stop.is_set():
                    cursor.execute(
                        """SELECT coalesce(wait_event_type, 'CPU'),
                                  coalesce(wait_event, 'running'), count(*)
                           FROM pg_stat_activity WHERE datname = current_database()
                           AND pid <> pg_backend_pid() AND state = 'active'
                           AND backend_type = 'client backend' GROUP BY 1, 2"""
                    )
                    self.samples += 1
                    for kind, event, count in cursor.fetchall():
                        self.active += count
                        self.waits[f"{kind}:{event}"] += count
                    cursor.execute(
                        """SELECT l.locktype, coalesce(c.relname, ''), l.mode, count(*)
                           FROM pg_locks l LEFT JOIN pg_class c ON c.oid = l.relation
                           WHERE NOT l.granted GROUP BY 1, 2, 3"""
                    )
                    total = 0
                    for locktype, rel, mode, count in cursor.fetchall():
                        self.ungranted[f"{locktype}:{rel}:{mode}"] += count
                        total += count
                    self.max_ungranted = max(self.max_ungranted, total)
                    time.sleep(SAMPLE_SECONDS)
        finally:
            connections["default"].close()

    def report(self):
        active = max(self.active, 1)
        session_waits = sum(
            n for key, n in self.ungranted.items() if ":tracer_capturesession:" in key
        )
        mxact_waits = sum(n for key, n in self.waits.items() if "MultiXact" in key)
        lock_waits = sum(n for key, n in self.waits.items() if key.startswith("Lock:"))
        return {
            "samples": self.samples,
            "active_backend_samples": self.active,
            "lock_wait_share": round(lock_waits / active, 4),
            "capturesession_lock_wait_samples": session_waits,
            "capturesession_lock_wait_share": round(session_waits / active, 4),
            "multixact_lwlock_wait_samples": mxact_waits,
            "max_ungranted_locks": self.max_ungranted,
            "wait_events": dict(self.waits.most_common(14)),
            "ungranted_locks": dict(self.ungranted.most_common(14)),
        }


class World:
    """One organization/workspace/user and its attachment scopes."""

    def __init__(self, user, scopes=1):
        self.user = user
        self.org = user.organization
        from accounts.models.workspace import Workspace

        self.workspace = Workspace.objects.get(organization=self.org, is_default=True)
        self.writer = broker.Writer("user", user.pk, self.org.id, self.workspace.id)
        self.scopes = [self._scope(i) for i in range(scopes)]

    def _scope(self, index):
        project = Project.objects.create(
            name=f"Load Project {index}",
            organization=self.org,
            workspace=self.workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="experiment",
        )
        return AttachmentScope.objects.create(
            project=project,
            workspace=self.workspace,
            organization=self.org,
            enabled=True,
            require_media_redaction=False,
        )

    def context(self):
        set_workspace_context(
            workspace=self.workspace, organization=self.org, user=self.user
        )

    def session_body(self, scope):
        return {
            "schema_versions": [1],
            "project_name": scope.project.name,
            "project_type": scope.project.trace_type,
            "capture_session_id": str(uuid.uuid4()),
            "correlation_nonce": str(uuid.uuid4()),
        }

    def upload_body(self, scope, session):
        return {
            "schema_version": 1,
            "attachment_id": str(uuid.uuid4()),
            "occurrence_id": str(uuid.uuid4()),
            "association": {
                "capture_session_id": str(session.client_id),
                "correlation_nonce": str(session.correlation_nonce),
                "parent": {
                    "trace_id": TRACE,
                    "span_id": f"{random.getrandbits(64):016x}",
                },
            },
            "policy_version": str(scope.policy_version),
            "deletion_generation": scope.deletion_generation,
            "mime_type": "audio/wav",
            "modality": "audio",
            "direction": "mixed",
            "source_kind": "Bytes",
            "declared_bytes": len(WAV),
            "message_index": 0,
            "part_index": 0,
            "role": "assistant",
        }


def raw_rows(scope, session, *, now):
    """The service's three inserts (uploads.initialize) without its locks."""
    common = {
        "scope": scope,
        "policy_version": scope.policy_version,
        "deletion_generation": scope.deletion_generation,
    }
    attachment = Attachment.objects.create(
        **common,
        client_id=uuid.uuid4(),
        modality="audio",
        mime_type="audio/wav",
        source_kind="Bytes",
    )
    occurrence = AttachmentOccurrence.objects.create(
        **common,
        client_id=uuid.uuid4(),
        attachment=attachment,
        capture_session=session,
        trace_id=uuid.UUID(TRACE),
        span_id=f"{random.getrandbits(64):016x}",
        direction="mixed",
        expires_at=now + timedelta(hours=24),
        message_index=0,
        part_index=0,
        role="assistant",
    )
    upload = AttachmentUpload.objects.create(
        **common,
        capture_session=session,
        attachment=attachment,
        occurrence=occurrence,
        idempotency_key=f"up-{occurrence.client_id.hex}",
        request_fingerprint="0" * 64,
        expected_bytes=len(WAV),
        mime_type="audio/wav",
        expires_at=now + timedelta(minutes=30),
    )
    return occurrence, upload


def run_load(world, *, shape, writers, fences, fk):
    scope = world.scopes[0]
    scope.refresh_from_db()
    world.context()
    body = world.session_body(scope)
    session, _ = broker.bootstrap(world.writer, body)
    store = ImmutableStore()
    stop, sampler_stop = threading.Event(), threading.Event()
    lat = defaultdict(list)
    errors = Counter()
    revoked = {}
    after_revoke = Counter()
    start_gate = threading.Barrier(writers + 1)

    def note_success(kind):
        if "commit" in revoked:
            after_revoke[kind] += 1

    def service_writer():
        world.context()
        try:
            start_gate.wait()
            while not stop.is_set():
                upload_body = world.upload_body(scope, session)
                key = f"up-{uuid.UUID(upload_body['occurrence_id']).hex}"
                t0 = time.perf_counter()
                try:
                    upload, token, _ = uploads.initialize(
                        world.writer, scope_id=scope.id, body=upload_body, key=key
                    )
                    lat["insert"].append(time.perf_counter() - t0)
                    note_success("initialize")
                    t0 = time.perf_counter()
                    uploads.transfer(
                        token,
                        scope_id=scope.id,
                        upload_id=upload.id,
                        chunks=[WAV],
                        storage=store,
                    )
                    lat["update_transfer"].append(time.perf_counter() - t0)
                    note_success("transfer")
                    t0 = time.perf_counter()
                    uploads.complete(token, scope_id=scope.id, upload_id=upload.id)
                    lat["update_complete"].append(time.perf_counter() - t0)
                    note_success("complete")
                except AttachmentError as exc:
                    errors[exc.code] += 1
                    if "commit" in revoked:
                        time.sleep(0.05)
        finally:
            connections["default"].close()

    def raw_writer():
        world.context()
        try:
            start_gate.wait()
            while not stop.is_set():
                t0 = time.perf_counter()
                try:
                    with transaction.atomic():
                        occurrence, upload = raw_rows(
                            scope, session, now=timezone.now()
                        )
                except Exception as exc:  # noqa: BLE001 - counted, never hidden
                    errors[type(exc).__name__] += 1
                    continue
                lat["insert"].append(time.perf_counter() - t0)
                note_success("insert")
                t0 = time.perf_counter()
                with transaction.atomic():
                    AttachmentUpload.objects.filter(pk=upload.pk).update(
                        state="validating", received_bytes=len(WAV)
                    )
                    AttachmentOccurrence.objects.filter(pk=occurrence.pk).update(
                        bound_at=timezone.now(), source_checked_at=timezone.now()
                    )
                lat["update"].append(time.perf_counter() - t0)
        finally:
            connections["default"].close()

    def renewer():
        world.context()
        try:
            while not stop.wait(RENEW_SECONDS):
                t0 = time.perf_counter()
                try:
                    broker.bootstrap(world.writer, body)
                    lat["renew"].append(time.perf_counter() - t0)
                except AttachmentError as exc:
                    errors[f"renew:{exc.code}"] += 1
        finally:
            connections["default"].close()

    def revoker():
        try:
            if stop.wait(SECONDS * 0.75):
                return
            t0 = time.perf_counter()
            revoked["start"] = time.time()
            CaptureSession.objects.filter(pk=session.pk).update(
                revoked_at=timezone.now()
            )
            revoked["commit"] = time.time()
            lat["revoke"].append(time.perf_counter() - t0)
        finally:
            connections["default"].close()

    target = service_writer if shape == "service" else raw_writer
    threads = [threading.Thread(target=target, daemon=True) for _ in range(writers)]
    helpers = [
        threading.Thread(target=renewer, daemon=True),
        threading.Thread(target=revoker, daemon=True),
    ]
    sampler = Sampler(sampler_stop)
    before = multixact()
    sql("SELECT pg_stat_reset_slru(NULL)", fetch=False)
    sampler.start()
    for thread in threads:
        thread.start()
    started = time.perf_counter()
    start_gate.wait()
    for thread in helpers:
        thread.start()
    time.sleep(SECONDS)
    stop.set()
    for thread in threads + helpers:
        thread.join(timeout=120)
    elapsed = time.perf_counter() - started
    sampler_stop.set()
    sampler.join(timeout=10)
    after = multixact()
    inserts = len(lat["insert"])
    members = after["next_multi_offset"] - before["next_multi_offset"]
    multis = after["next_multixact_id"] - before["next_multixact_id"]
    rate = inserts / elapsed if elapsed else 0
    per_insert = members / inserts if inserts else 0
    days_to_2_31 = (
        round(2**31 / (per_insert * rate) / 86400, 2) if per_insert and rate else None
    )
    result = {
        "shape": shape,
        "writers": writers,
        "fences": fences,
        "capture_session_fk": fk,
        "seconds": round(elapsed, 2),
        "insert_tx_per_s": round(rate, 1),
        "latency": {kind: summary(values) for kind, values in sorted(lat.items())},
        "errors": dict(errors),
        "successes_after_revocation_commit": dict(after_revoke),
        "revocation_wall_ms": (
            round((revoked["commit"] - revoked["start"]) * 1000, 2)
            if "commit" in revoked
            else None
        ),
        "multixact": {
            "created": multis,
            "members_created": members,
            "members_per_insert_tx": round(per_insert, 3),
            "days_to_2_31_members_at_this_rate": days_to_2_31,
            "slru": after["slru"],
        },
        "locks": sampler.report(),
        "rows": {
            "occurrences": AttachmentOccurrence.objects.count(),
            "uploads": AttachmentUpload.objects.count(),
        },
    }
    if shape == "service":
        # Exact commit order (track_commit_timestamp=on): occurrences are
        # insert-only in this shape, and a failed renewal never rewrites the
        # revoked session row, so its xmin is the revocation's transaction.
        (late,) = sql(
            """SELECT count(*) FROM tracer_attachmentoccurrence o
               JOIN tracer_capturesession s ON s.id = o.capture_session_id
               WHERE s.revoked_at IS NOT NULL
               AND pg_xact_commit_timestamp(o.xmin) > pg_xact_commit_timestamp(s.xmin)"""
        )[0]
        result["occurrences_committed_after_revocation"] = late
    alive = sum(thread.is_alive() for thread in threads + helpers)
    result["threads_still_alive"] = alive
    return result


def write(name, payload):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(payload, indent=2, default=str))


def settings_snapshot():
    keys = (
        "server_version",
        "max_connections",
        "shared_buffers",
        "work_mem",
        "effective_cache_size",
        "synchronous_commit",
        "fsync",
        "wal_level",
        "max_wal_size",
        "checkpoint_timeout",
        "random_page_cost",
        "deadlock_timeout",
        "autovacuum_multixact_freeze_max_age",
        "track_commit_timestamp",
    )
    return {key: sql(f"SHOW {key}")[0][0] for key in keys}


def test_capture_session_contention_matrix(user, settings, monkeypatch):
    settings.FI_ATTACHMENTS_ENABLED = True
    monkeypatch.setattr(uploads, "BUDGETS", UNBOUNDED)
    world = World(user)
    fks = session_fks()
    assert len(fks) == 3, fks  # occurrence, upload, recording
    runs = []
    meta = {
        "postgres": settings_snapshot(),
        "capture_session_fks": fks,
        "writers": WRITERS,
        "seconds_per_run": SECONDS,
        "renew_every_s": RENEW_SECONDS,
    }
    write("meta", meta)
    plan = [
        ("raw", True, True),
        ("raw", False, True),
        ("raw", True, False),
        ("raw", False, False),
        ("service", True, True),
        ("service", False, True),
    ]
    for shape, fences, fk in plan:
        if fk:
            if not session_fks():
                restore_fks(fks)
        elif session_fks():
            drop_fks(fks)
        for writers in WRITERS:
            reset_rows()
            set_fences(fences)
            try:
                result = run_load(
                    world, shape=shape, writers=writers, fences=fences, fk=fk
                )
            finally:
                set_fences(True)
            runs.append(result)
            write("matrix", {"meta": meta, "runs": runs})
            print(
                json.dumps(
                    {
                        k: result[k]
                        for k in (
                            "shape",
                            "writers",
                            "fences",
                            "capture_session_fk",
                            "insert_tx_per_s",
                        )
                    }
                ),
                flush=True,
            )
    if not session_fks():
        restore_fks(fks)
    assert len(session_fks()) == 3
    # Every run did work: the harness never reports numbers from an idle run.
    assert all(run["latency"].get("insert", {}).get("n", 0) > 0 for run in runs)


# --- 100k backlog: EXPLAIN (ANALYZE, BUFFERS) ---------------------------------


def seed_backlog(world, scope, rows, *, hot):
    session, _ = broker.bootstrap(world.writer, world.session_body(scope))
    now = timezone.now()
    common = {
        "scope": scope,
        "policy_version": scope.policy_version,
        "deletion_generation": scope.deletion_generation,
    }
    batch = 5000
    for offset in range(0, rows, batch):
        size = min(batch, rows - offset)
        attachments = Attachment.objects.bulk_create(
            [
                Attachment(**common, client_id=uuid.uuid4(), source_kind="Bytes")
                for _ in range(size)
            ]
        )
        occurrences = []
        for index, attachment in enumerate(attachments):
            n = offset + index
            bound = n % 6 == 0  # ~1/6 bound (orphan sweep), the rest unbound
            occurrences.append(
                AttachmentOccurrence(
                    **common,
                    client_id=uuid.uuid4(),
                    attachment=attachment,
                    capture_session=session,
                    trace_id=uuid.uuid4(),
                    span_id=f"{n:016x}",
                    direction="mixed",
                    bound_at=now - timedelta(hours=48) if bound else None,
                    source_checked_at=(now - timedelta(minutes=n % 600))
                    if bound and n % 2
                    else None,
                    source_expires_at=(now - timedelta(hours=1))
                    if bound and n % 12 == 0
                    else (now + timedelta(days=30) if bound else None),
                    expires_at=now - timedelta(minutes=1 + n % 600)
                    if hot
                    else now + timedelta(hours=1),
                )
            )
        occurrences = AttachmentOccurrence.objects.bulk_create(occurrences)
        if not hot:
            continue
        upload_rows = AttachmentUpload.objects.bulk_create(
            [
                AttachmentUpload(
                    **common,
                    capture_session=session,
                    attachment=occurrence.attachment,
                    occurrence=occurrence,
                    idempotency_key=f"seed-{occurrence.client_id.hex}",
                    request_fingerprint="0" * 64,
                    expected_bytes=len(WAV),
                    mime_type="audio/wav",
                    expires_at=now - timedelta(minutes=1),
                )
                for occurrence in occurrences
            ]
        )
        UploadReservation.objects.bulk_create(
            [
                UploadReservation(
                    scope=scope,
                    upload=upload,
                    reserved_bytes=len(WAV),
                    decoded_bytes=len(WAV),
                    expires_at=now - timedelta(seconds=1 + i),
                )
                for i, upload in enumerate(upload_rows)
            ]
        )
        AttachmentOutbox.objects.bulk_create(
            [
                AttachmentOutbox(
                    scope=scope,
                    upload=upload,
                    attachment_id=upload.attachment_id,
                    event="validate",
                    generation=scope.deletion_generation,
                    available_at=now - timedelta(seconds=i),
                )
                for i, upload in enumerate(upload_rows)
            ]
        )


def binding_claim_sql(cap=512, limit=256):
    # #3941 8990cb4fe control_plane.py:147-180 (claim_pending), ORM -> SQL by hand.
    return f"""
    SELECT id, scope_id FROM (
      SELECT o.id, o.scope_id, COALESCE(o.bind_checked_at, o.created_at) AS touched,
             ROW_NUMBER() OVER (PARTITION BY o.scope_id
               ORDER BY COALESCE(o.bind_checked_at, o.created_at) ASC, o.id ASC) AS turn
      FROM tracer_attachmentoccurrence o
      JOIN tracer_attachmentscope s ON s.id = o.scope_id
      JOIN tracer_attachment a ON a.id = o.attachment_id
      WHERE o.bound_at IS NULL AND o.deleted_at IS NULL AND s.enabled
        AND NOT (a.state IN ('deleted', 'expired'))
    ) q WHERE turn <= {cap} ORDER BY turn, touched, id LIMIT {limit}"""


def explain(query, params=None):
    rows = sql("EXPLAIN (ANALYZE, BUFFERS) " + query, params)
    return [row[0] for row in rows]


def test_backlog_query_plans(user, settings):
    settings.FI_ATTACHMENTS_ENABLED = True
    world = World(user, scopes=10)
    world.context()
    hot, *cold = world.scopes
    t0 = time.perf_counter()
    seed_backlog(world, hot, BACKLOG, hot=True)
    for scope in cold:
        seed_backlog(world, scope, BACKLOG // 50, hot=False)
    seeded_s = time.perf_counter() - t0
    # #3941 0133_attachment_binding_claims (becomes 0134): column + partial index.
    sql(
        "ALTER TABLE tracer_attachmentoccurrence ADD COLUMN bind_checked_at timestamptz NULL",
        fetch=False,
    )
    sql(
        "CREATE INDEX attachment_unbound_claim_idx ON tracer_attachmentoccurrence "
        "(created_at, id) WHERE bound_at IS NULL",
        fetch=False,
    )
    for table in (
        "attachmentoccurrence",
        "attachment",
        "attachmentupload",
        "uploadreservation",
        "attachmentoutbox",
        "attachmentscope",
    ):
        sql(f"VACUUM ANALYZE tracer_{table}", fetch=False)
    now = timezone.now()
    plans = {}
    plans["expire_reservations"] = (
        UploadReservation.objects.filter(settled_at__isnull=True, expires_at__lte=now)
        .order_by("expires_at")[:100]
        .explain(analyze=True, buffers=True)
    )
    plans["expire_occurrences"] = (
        AttachmentOccurrence.objects.filter(scope=hot, deleted_at__isnull=True)
        .filter(Q(expires_at__lte=now) | Q(source_expires_at__lte=now))
        .order_by("id")[:100]
        .explain(analyze=True, buffers=True)
    )
    plans["outbox_due"] = (
        AttachmentOutbox.objects.filter(
            event="validate", processed_at__isnull=True, available_at__lte=now
        )
        .order_by("available_at", "id")
        .values_list("pk", flat=True)[:100]
        .explain(analyze=True, buffers=True)
    )
    plans["orphan_sweep"] = (
        AttachmentOccurrence.objects.filter(
            deleted_at__isnull=True,
            bound_at__isnull=False,
            bound_at__lte=now - timedelta(hours=24),
        )
        .order_by(F("source_checked_at").asc(nulls_first=True), "id")
        .values(
            "pk",
            "scope_id",
            "scope__project_id",
            "trace_id",
            "span_id",
            "source_missing_since",
        )[:200]
        .explain(analyze=True, buffers=True)
    )
    plans["parent_lookup"] = AttachmentOccurrence.objects.filter(
        scope=hot, trace_id=uuid.uuid4(), span_id="0000000000000006"
    ).explain(analyze=True, buffers=True)
    plans["binding_claim_3941"] = "\n".join(explain(binding_claim_sql()))
    counts = {
        "occurrences": AttachmentOccurrence.objects.count(),
        "unbound": AttachmentOccurrence.objects.filter(bound_at__isnull=True).count(),
        "expired_hot": AttachmentOccurrence.objects.filter(
            scope=hot, expires_at__lte=now
        ).count(),
        "reservations_unsettled_expired": UploadReservation.objects.filter(
            settled_at__isnull=True, expires_at__lte=now
        ).count(),
        "outbox_due": AttachmentOutbox.objects.filter(
            processed_at__isnull=True
        ).count(),
        "seed_seconds_fences_on": round(seeded_s, 1),
    }
    write("backlog", {"counts": counts, "plans": plans})
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "backlog-plans.txt", "w") as handle:
        handle.write(json.dumps(counts, indent=2) + "\n")
        for name, plan in plans.items():
            handle.write(f"\n=== {name}\n{plan}\n")
    assert counts["unbound"] >= BACKLOG * 0.8
    assert counts["reservations_unsettled_expired"] == BACKLOG
