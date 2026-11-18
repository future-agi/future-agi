"""Isolated service behavior tests with in-memory ORM/transaction doubles.

These execute production capacity/accounting/publication/upload/validation code,
not PostgreSQL locks or triggers. Concurrent SQL/stack qualification is separate.
The doubles record lock requests and preserve row identity; no application apps,
credentials, external storage, network, real decoder or database are loaded.
"""

import ast
import importlib.util
import os
import sys
import types
from contextlib import ContextDecorator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

ROOT = Path(os.environ.get("ATT_UPLOAD_SOURCE", Path(__file__).parents[1]))
SERVICE = ROOT / "services" / "attachments"
NOW = datetime(2026, 11, 16, 21, tzinfo=UTC)


class Row:
    def __init__(self, **values):
        self.__dict__.update(values)
        self.pk = values.get("id", values.get("key"))
        self.saved = []

    def save(self, *, update_fields):
        self.saved.append(tuple(update_fields))


class Query:
    def __init__(self, manager, values=None, locked=False):
        self.manager = manager
        self.values = manager.rows if values is None else values
        self.locked = locked

    def select_for_update(self, **_):
        return Query(self.manager, self.values, True)

    def select_related(self, *_):
        return self

    def only(self, *_):
        return self

    def filter(self, **kwargs):
        def matches(row):
            for key, value in kwargs.items():
                field, _, op = key.partition("__")
                actual = getattr(row, field)
                if op == "in":
                    equal = actual in value
                elif op == "isnull":
                    equal = (actual is None) == value
                elif op == "lte":
                    equal = actual <= value
                else:
                    equal = actual == value
                if not equal:
                    return False
            return True

        return Query(self.manager, [r for r in self.values if matches(r)], self.locked)

    def get(self, **kwargs):
        values = self.filter(**kwargs).values
        if len(values) != 1:
            raise self.manager.model.DoesNotExist
        row = values[0]
        if self.locked:
            self.manager.locks.append((self.manager.model.__name__, row.pk))
        return row

    def first(self):
        return self.values[0] if self.values else None

    def exists(self):
        return bool(self.values)

    def order_by(self, *fields):
        self.values = sorted(
            self.values, key=lambda row: tuple(getattr(row, field) for field in fields)
        )
        return self

    def __iter__(self):
        if self.locked:
            for row in self.values:
                self.manager.locks.append((self.manager.model.__name__, row.pk))
        return iter(self.values)

    def __getitem__(self, item):
        return self.values[item]

    def update(self, **values):
        for row in self.values:
            row.__dict__.update(values)
        return len(self.values)


class Manager(Query):
    def __init__(self, model, locks):
        self.rows, self.model, self.locks = [], model, locks
        super().__init__(self)

    def create(self, **kwargs):
        kwargs.setdefault("id", uuid4())
        row = self.model(**kwargs)
        self.rows.append(row)
        return row

    def get_or_create(self, defaults=None, **kwargs):
        row = self.filter(**kwargs).first()
        if row is not None:
            return row, False
        values = {**(defaults or {}), **kwargs}
        if self.model.__name__ == "AttachmentBudget":
            for name in (
                "reserved_bytes",
                "active_transfers",
                "validation_jobs",
                "active_recordings",
                "object_count",
                "link_count",
                "initialization_attempts",
            ):
                values.setdefault(name, 0)
            values["id"] = values["key"]
        return self.create(**values), True


@pytest.fixture
def env(monkeypatch):
    locks = []
    connection = SimpleNamespace(
        in_atomic_block=False, needs_rollback=False, atomic_blocks=[]
    )

    class Atomic(ContextDecorator):
        def __enter__(self):
            connection.atomic_blocks.append(self)
            connection.in_atomic_block = True
            return self

        def __exit__(self, *_):
            connection.atomic_blocks.pop()
            connection.in_atomic_block = bool(connection.atomic_blocks)

    def atomic(function=None):
        return Atomic() if function is None else Atomic()(function)

    def module(name, **attrs):
        value = types.ModuleType(name)
        value.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, value)
        if "." in name:
            parent, _, child = name.rpartition(".")
            if parent in sys.modules:
                monkeypatch.setattr(sys.modules[parent], child, value, raising=False)
        return value

    for name in (
        "django",
        "django.utils",
        "tracer",
        "tracer.models",
        "tracer.services",
        "tracer.services.attachments",
        "accounts",
    ):
        module(name, __path__=[])
    tx = module(
        "django.db.transaction", atomic=atomic, get_connection=lambda: connection
    )
    module(
        "django.db",
        transaction=tx,
        IntegrityError=type("IntegrityError", (Exception,), {}),
    )
    module("django.utils.timezone", now=lambda: NOW)
    names = {
        "tracer.models.attachment": [
            "Attachment",
            "AttachmentBudget",
            "AttachmentUpload",
            "AttachmentScope",
            "AttachmentOutbox",
            "AttachmentAuditEvent",
            "AttachmentOccurrence",
            "AttachmentRecording",
            "CaptureSession",
            "AttachmentWriteGrant",
        ],
        "tracer.models.attachment_recording": [
            "AttachmentImportJob",
            "AttachmentRecordingDerivative",
            "AttachmentRecordingSegment",
            "AttachmentProviderConnection",
            "AttachmentSourceEnvelope",
            "AttachmentProviderRecording",
        ],
        "accounts.models": ["User", "OrgApiKey"],
    }
    models = {}
    for name, classes in names.items():
        attrs = {}
        for cls in classes:
            model = type(
                cls, (Row,), {"DoesNotExist": type("DoesNotExist", (Exception,), {})}
            )
            model.objects = model.no_workspace_objects = Manager(model, locks)
            models[cls] = attrs[cls] = model
        module(name, **attrs)
    state = SimpleNamespace(
        PENDING="pending_upload",
        VALIDATING="validating",
        READY="ready",
        FAILED="failed",
        REJECTED="rejected",
        EXPIRED="expired",
        DELETED="deleted",
    )
    sys.modules["tracer.models.attachment"].AttachmentState = state

    def load(name):
        fullname = f"tracer.services.attachments.{name}"
        spec = importlib.util.spec_from_file_location(fullname, SERVICE / f"{name}.py")
        value = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, fullname, value)
        monkeypatch.setattr(
            sys.modules["tracer.services.attachments"], name, value, raising=False
        )
        spec.loader.exec_module(value)
        return value

    interfaces, capacity, contract = (
        load("interfaces"),
        load("capacity"),
        load("contract"),
    )
    db, accounting, publication = (
        load("capacity_db"),
        load("accounting"),
        load("publication"),
    )
    module(
        "tracer.services.attachments.bootstrap",
        CAPABILITY_SECONDS=300,
        LIMITS={
            "max_upload_bytes": 8 * 1024**2,
            "transfer_timeout_seconds": 60,
            "segment_duration_ms": 30000,
        },
        authorize=lambda *a, **k: None,
        capability=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no capability expected")
        ),
        check_session=lambda session: models["AttachmentScope"].objects.get(
            pk=session.scope_id
        ),
        check_writer=lambda *a: None,
        find_session=lambda *a: None,
        session_policy=lambda session: session.policy,
    )
    module("tracer.services.attachments.probe", REJECTED_EXIT=65)
    uploads, validation = load("uploads"), load("validation")
    result = SimpleNamespace(
        models=models,
        locks=locks,
        tx=tx,
        connection=connection,
        i=interfaces,
        c=capacity,
        db=db,
        a=accounting,
        p=publication,
        u=uploads,
        v=validation,
        error=contract.AttachmentError,
    )

    def scope(org=None, project=None):
        return models["AttachmentScope"].objects.create(
            organization_id=org or uuid4(),
            project_id=project or uuid4(),
            workspace_id=uuid4(),
            enabled=True,
            allow_import=True,
            policy_version=1,
            deletion_generation=0,
        )

    def owner(scope, kind="upload", pool=None, **fields):
        ident = uuid4()
        producer = fields.pop("producer_kind", "authenticated_stream")
        pool = pool or (
            capacity.upload_pool_key(ident)
            if kind == "upload" and producer == "authenticated_stream"
            else capacity.DERIVATIVE_POOL_KEY
            if kind == "derivative"
            else capacity.IMPORT_POOL_KEY
        )
        model = models[
            {
                "upload": "AttachmentUpload",
                "import": "AttachmentImportJob",
                "derivative": "AttachmentRecordingDerivative",
            }[kind]
        ]
        row = model.objects.create(
            id=ident,
            scope=scope,
            scope_id=scope.id,
            producer_kind=producer,
            reservation_settled_at=None,
            cleanup_not_before=NOW,
            cleanup_checked_at=NOW,
            unowned_cleanup_due_at=None,
            transfer_started_at=None,
            lease_token=None,
            lease_until=None,
            usage_id=None,
            **db.reservation_fields(
                pool_key=pool, expires_at=NOW + timedelta(hours=1), now=NOW
            ),
            **fields,
        )
        return row, interfaces.ReservationOwner(kind, ident, scope.id)

    result.scope, result.owner = scope, owner
    return result


def test_pool_locks_are_numeric_then_org_project(env):
    scope = env.scope()
    with env.tx.atomic():
        env.db.lock_capacity(
            scope_id=scope.id,
            pool_keys=("global:derivatives", "global:10", "global:2", "global:imports"),
        )
    assert [key for _, key in env.locks] == [
        "global:2",
        "global:10",
        "global:imports",
        "global:derivatives",
        f"org:{scope.organization_id}",
        f"project:{scope.project_id}",
    ]


def test_handles_cannot_escape_transaction(env):
    scope = env.scope()
    row, owner = env.owner(scope)
    with env.tx.atomic():
        handle = env.db.lock_capacity(
            scope_id=scope.id, pool_keys=(row.reservation_pool_key,)
        )
    with env.tx.atomic(), pytest.raises(RuntimeError):
        env.db.reserve_locked(
            handle,
            owner=owner,
            amount=env.i.ResourceVector(1, 0, 0, 0, 0, 0),
            expires_at=row.reservation_expires_at,
        )


@pytest.mark.parametrize("component", range(6))
def test_each_global_component_saturates_without_extra_import_pool(env, component):
    pool = "global:imports"
    total = env.c.pool_cap(pool)
    values = [0] * 6
    values[component] = env.db._values(total)[component]
    # Distinct tenants avoid scoped limits; distribute enough owners to reach
    # the actual import pool ceiling in each component.
    remaining = values[component]
    while remaining:
        scope = env.scope()
        row, owner = env.owner(scope, "import")
        quantity = min(remaining, env.db._values(env.c.PROJECT_CAP)[component])
        vector = [0] * 6
        vector[component] = quantity
        with env.tx.atomic():
            handle = env.db.lock_capacity(scope_id=scope.id, pool_keys=(pool,))
            env.db.reserve_locked(
                handle,
                owner=owner,
                amount=env.i.ResourceVector(*vector),
                expires_at=row.reservation_expires_at,
            )
        remaining -= quantity
    scope = env.scope()
    row, owner = env.owner(scope, "import")
    vector = [0] * 6
    vector[component] = 1
    with env.tx.atomic(), pytest.raises(env.error, match="throttled"):
        handle = env.db.lock_capacity(scope_id=scope.id, pool_keys=(pool,))
        env.db.reserve_locked(
            handle,
            owner=owner,
            amount=env.i.ResourceVector(*vector),
            expires_at=row.reservation_expires_at,
        )
    assert env.db._budget_vector(
        env.models["AttachmentBudget"].objects.get(pk=pool)
    ) == env.i.ResourceVector(*values)


@pytest.mark.parametrize("kind", ["upload", "import", "derivative"])
def test_all_producers_share_project_totals(env, kind):
    scope = env.scope()
    import_row, import_owner = env.owner(scope, "import")
    row, owner = env.owner(scope, kind)
    cap = env.c.PROJECT_CAP.bytes
    with env.tx.atomic():
        handle = env.db.lock_capacity(
            scope_id=scope.id,
            pool_keys=tuple(
                dict.fromkeys(
                    (import_row.reservation_pool_key, row.reservation_pool_key)
                )
            ),
        )
        env.db.reserve_locked(
            handle,
            owner=import_owner,
            amount=env.i.ResourceVector(cap, 0, 0, 0, 0, 0),
            expires_at=import_row.reservation_expires_at,
        )
        with pytest.raises(env.error, match="throttled"):
            env.db.reserve_locked(
                handle,
                owner=owner,
                amount=env.i.ResourceVector(1, 0, 0, 0, 0, 0),
                expires_at=row.reservation_expires_at,
            )
    assert env.db.owner_vector(row) == env.c.ZERO


def test_projects_share_organization_and_orgs_are_isolated(env):
    org = uuid4()
    for _ in range(2):
        scope = env.scope(org=org)
        row, owner = env.owner(scope, "import")
        with env.tx.atomic():
            handle = env.db.lock_capacity(
                scope_id=scope.id, pool_keys=("global:imports",)
            )
            env.db.reserve_locked(
                handle,
                owner=owner,
                amount=env.i.ResourceVector(1024**3, 0, 0, 0, 0, 0),
                expires_at=row.reservation_expires_at,
            )
    blocked_scope = env.scope(org=org)
    row, owner = env.owner(blocked_scope, "derivative")
    with env.tx.atomic(), pytest.raises(env.error, match="throttled"):
        handle = env.db.lock_capacity(
            scope_id=blocked_scope.id, pool_keys=(row.reservation_pool_key,)
        )
        env.db.reserve_locked(
            handle,
            owner=owner,
            amount=env.i.ResourceVector(1, 0, 0, 0, 0, 0),
            expires_at=row.reservation_expires_at,
        )
    other = env.scope()
    row, owner = env.owner(other, "derivative")
    with env.tx.atomic():
        handle = env.db.lock_capacity(
            scope_id=other.id, pool_keys=(row.reservation_pool_key,)
        )
        env.db.reserve_locked(
            handle,
            owner=owner,
            amount=env.i.ResourceVector(1, 0, 0, 0, 0, 0),
            expires_at=row.reservation_expires_at,
        )


def test_single_import_transfer_neutral_replay_and_separate_release(env):
    scope = env.scope()
    job, job_owner = env.owner(scope, "import")
    upload, upload_owner = env.owner(scope, producer_kind="import")
    u, j = env.c.single_import_peaks(
        source_bytes=100, decoder_scratch_bytes=20, extra_reader_scratch_bytes=7
    )
    with env.tx.atomic():
        handle = env.db.lock_capacity(scope_id=scope.id, pool_keys=("global:imports",))
        env.db.reserve_locked(
            handle, owner=job_owner, amount=u + j, expires_at=job.reservation_expires_at
        )
        before = [
            env.db._budget_vector(row) for row in env.db._rows(handle, "global:imports")
        ]
        env.db.reserve_locked(
            handle, owner=job_owner, amount=u + j, expires_at=job.reservation_expires_at
        )
        env.db.transfer_locked(
            handle, from_owner=job_owner, to_owner=upload_owner, amount=u
        )
        assert env.db.owner_vector(job) == j
        assert env.db.owner_vector(upload) == u
        assert before == [
            env.db._budget_vector(row) for row in env.db._rows(handle, "global:imports")
        ]
        with pytest.raises(ValueError):
            env.db.transfer_locked(
                handle, from_owner=job_owner, to_owner=upload_owner, amount=u
            )
        upload.usage_id = uuid4()
        assert env.db.release_locked(
            handle, owner=upload_owner, cleanup_complete=True, now=NOW
        )
        assert not env.db.release_locked(
            handle, owner=upload_owner, cleanup_complete=True, now=NOW
        )
        assert env.db._budget_vector(env.db._rows(handle, "global:imports")[0]) == j
        assert env.db.release_locked(
            handle, owner=job_owner, cleanup_complete=True, now=NOW
        )
        assert (
            env.db._budget_vector(env.db._rows(handle, "global:imports")[0])
            == env.c.ZERO
        )
        assert env.db.owner_vector(upload) == u


@pytest.mark.parametrize(
    "field,value",
    [
        ("cleanup_checked_at", None),
        ("cleanup_not_before", NOW + timedelta(seconds=1)),
        ("unowned_cleanup_due_at", NOW),
        ("transfer_started_at", NOW),
        ("lease_token", UUID(int=9)),
        ("lease_until", NOW),
    ],
)
def test_cleanup_failures_hold_charge(env, field, value):
    scope = env.scope()
    row, owner = env.owner(scope)
    amount = env.i.ResourceVector(100, 1, 1, 0, 4, 1)
    row.usage_id = uuid4()
    with env.tx.atomic():
        handle = env.db.lock_capacity(
            scope_id=scope.id, pool_keys=(row.reservation_pool_key,)
        )
        env.db.reserve_locked(
            handle, owner=owner, amount=amount, expires_at=row.reservation_expires_at
        )
        setattr(row, field, value)
        assert not env.db.release_locked(
            handle, owner=owner, cleanup_complete=True, now=NOW
        )
        assert (
            env.db._budget_vector(env.db._rows(handle, row.reservation_pool_key)[0])
            == amount
        )


def test_counter_underflow_is_not_clamped(env):
    scope = env.scope()
    row, owner = env.owner(scope)
    row.usage_id = uuid4()
    with env.tx.atomic():
        handle = env.db.lock_capacity(
            scope_id=scope.id, pool_keys=(row.reservation_pool_key,)
        )
        env.db.reserve_locked(
            handle,
            owner=owner,
            amount=env.i.ResourceVector(100, 0, 0, 0, 0, 0),
            expires_at=row.reservation_expires_at,
        )
        budget = env.db._rows(handle, row.reservation_pool_key)[1]
        budget.reserved_bytes = 99
        with pytest.raises(ValueError):
            env.db.release_locked(handle, owner=owner, cleanup_complete=True, now=NOW)
        assert row.reservation_settled_at is None
        assert env.db._rows(handle, row.reservation_pool_key)[0].reserved_bytes == 100


def test_impossible_peak_is_not_temporary_throttle(env):
    scope = env.scope()
    row, owner = env.owner(scope)
    with env.tx.atomic(), pytest.raises(env.error, match="too_large"):
        handle = env.db.lock_capacity(
            scope_id=scope.id, pool_keys=(row.reservation_pool_key,)
        )
        env.db.reserve_locked(
            handle,
            owner=owner,
            amount=env.i.ResourceVector(
                env.c.UPLOAD_SHARD_CAP.bytes + 1, 0, 0, 0, 0, 0
            ),
            expires_at=row.reservation_expires_at,
        )


def test_rate_attempts_shared_between_producers(env):
    scope = env.scope()
    for _ in range(120):
        assert env.db.count_attempt(
            scope_id=scope.id, pool_key="global:imports", now=NOW
        )
    assert not env.db.count_attempt(
        scope_id=scope.id, pool_key="global:derivatives", now=NOW
    )
    assert (
        env.models["AttachmentBudget"]
        .objects.get(pk=f"project:{scope.project_id}")
        .initialization_attempts
        == 121
    )
    assert env.db.count_attempt(
        scope_id=scope.id, pool_key="global:derivatives", now=NOW + timedelta(minutes=1)
    )


def terminal_upload(env):
    scope = env.scope()
    asset = env.models["Attachment"].objects.create(
        scope_id=scope.id,
        verified_blob_id=uuid4(),
        verified_byte_length=7,
        verified_storage_version="v1",
        state="deleted",
        deleted_at=NOW,
    )
    upload, _ = env.owner(
        scope,
        attachment_id=asset.id,
        state="deleted",
        usage_created_at=None,
        usage_outcome=None,
        usage_stored_bytes=None,
        settlement_event_id=None,
        admission_event_id=uuid4(),
    )
    return scope, asset, upload


def test_delete_before_accounting_preserves_ready_and_original_event(env):
    scope, asset, upload = terminal_upload(env)
    event = env.models["AttachmentOutbox"].objects.create(
        scope=scope,
        scope_id=scope.id,
        upload_id=upload.id,
        event="settled",
        processed_at=None,
    )
    result = env.a.consume_settlement(event.id)
    assert result is upload
    assert (
        upload.usage_outcome,
        upload.usage_stored_bytes,
        upload.settlement_event_id,
    ) == ("ready", 7, event.id)
    first = (
        upload.usage_id,
        upload.usage_created_at,
        upload.usage_outcome,
        upload.usage_stored_bytes,
    )
    env.a.consume_settlement(event.id)
    assert first == (
        upload.usage_id,
        upload.usage_created_at,
        upload.usage_outcome,
        upload.usage_stored_bytes,
    )
    assert len(env.models["AttachmentAuditEvent"].objects.rows) == 1
    assert [name for name, _ in env.locks[:3]] == [
        "AttachmentUpload",
        "Attachment",
        "AttachmentOutbox",
    ]


def test_existing_receipt_survives_later_deleted_outcome(env):
    _, asset, upload = terminal_upload(env)
    with env.tx.atomic():
        assert env.a.receipt_locked(upload, attachment=asset, outcome="deleted")
        old = (
            upload.usage_id,
            upload.usage_created_at,
            upload.usage_outcome,
            upload.settlement_event_id,
        )
        assert not env.a.receipt_locked(upload, attachment=asset, outcome="expired")
        assert old == (
            upload.usage_id,
            upload.usage_created_at,
            upload.usage_outcome,
            upload.settlement_event_id,
        )


def test_admission_legacy_event_preserves_identity_and_lock_order(env):
    scope, _, upload = terminal_upload(env)
    event = env.models["AttachmentOutbox"].objects.create(
        id=upload.admission_event_id,
        scope=scope,
        scope_id=scope.id,
        upload_id=upload.id,
        event="admitted",
        processed_at=None,
    )
    assert env.a.consume_admission(event.id)
    assert not env.a.consume_admission(event.id)
    assert [name for name, _ in env.locks[:2]] == [
        "AttachmentUpload",
        "AttachmentOutbox",
    ]
    assert env.models["AttachmentAuditEvent"].objects.rows[0].event_id == event.id


def publication_upload(env, imported=False):
    scope = env.scope()
    binding = uuid4()
    asset = env.models["Attachment"].objects.create(
        scope_id=scope.id,
        storage_binding_id=binding,
        state="validating",
        deleted_at=None,
        policy_version=1,
        deletion_generation=0,
        mime_type="audio/wav",
        verified_blob_id=None,
    )
    upload, _ = env.owner(
        scope,
        producer_kind="import" if imported else "authenticated_stream",
        attachment_id=asset.id,
        attachment=asset,
        state="validating",
        serving_key="original/server-id",
        serving_version="v1",
        expected_bytes=100,
        received_bytes=100,
        quarantine_sha256="a" * 64,
        mime_type="audio/wav",
        expires_at=NOW + timedelta(minutes=10),
        allocation_epoch=2,
    )
    facts = env.i.VerifiedAssetFacts(
        env.i.StoredObject(
            env.i.ObjectIdentity(binding, "serving", upload.serving_key, "v1"),
            100,
            "a" * 64,
        ),
        "audio/wav",
        NOW,
        48000,
        1,
        48000,
    )
    return scope, asset, upload, facts


@pytest.mark.parametrize(
    "field,value",
    [
        ("storage_binding_id", UUID(int=20)),
        ("deleted_at", NOW),
        ("deletion_generation", 2),
        ("mime_type", "image/png"),
    ],
)
def test_publication_rejects_binding_tombstone_policy_and_mime(env, field, value):
    scope, asset, upload, facts = publication_upload(env)
    setattr(asset, field, value)
    with env.tx.atomic(), pytest.raises(env.error, match="validation_fenced"):
        env.p.publish_verified_locked(
            attachment=asset,
            facts=facts,
            expected_binding_id=facts.object.identity.binding_id,
            scope=scope,
            fence=None,
            allocation=upload,
        )
    assert asset.verified_blob_id is None


def test_publication_writes_embedded_facts_once(env):
    scope, asset, upload, facts = publication_upload(env)
    with env.tx.atomic():
        for _ in range(2):
            env.p.publish_verified_locked(
                attachment=asset,
                facts=facts,
                expected_binding_id=asset.storage_binding_id,
                scope=scope,
                fence=None,
                allocation=upload,
            )
    assert asset.state == "ready"
    assert asset.verified_byte_length == 100
    assert len(asset.saved) == 1


@pytest.mark.parametrize("mutation", ["token", "epoch", "expiry", "phase", "binding"])
def test_import_publication_requires_exact_job_fence(env, mutation):
    scope, asset, upload, facts = publication_upload(env, imported=True)
    job, _ = env.owner(
        scope,
        "import",
        upload_id=upload.id,
        state="validating",
        deadline=NOW + timedelta(minutes=10),
        lease_epoch=2,
        lease_phase="writing",
        storage_binding_id=asset.storage_binding_id,
    )
    job.lease_token, job.lease_until = uuid4(), NOW + timedelta(minutes=5)
    fence = env.i.LeaseFence("import", job.id, job.lease_token, 2, job.lease_until)
    if mutation == "token":
        job.lease_token = uuid4()
    elif mutation == "epoch":
        job.lease_epoch = 3
    elif mutation == "expiry":
        job.lease_until = NOW
    elif mutation == "phase":
        job.lease_phase = "terminal"
    else:
        job.storage_binding_id = uuid4()
    with env.tx.atomic(), pytest.raises(env.error, match="validation_fenced"):
        env.p.publish_verified_locked(
            attachment=asset,
            facts=facts,
            expected_binding_id=asset.storage_binding_id,
            scope=scope,
            fence=fence,
            allocation=upload,
        )


def source_rows(env):
    scope = env.scope()
    creator = uuid4()
    session = Row(
        id=uuid4(),
        writer_kind="user",
        writer_id=creator,
        expires_at=NOW + timedelta(hours=1),
        policy_version=1,
        deletion_generation=0,
    )
    job = Row(
        id=uuid4(),
        capture_session_id=session.id,
        importer_kind="user",
        importer_id=creator,
        source_creator_kind="user",
        source_creator_id=creator,
        source_generation_kind="http_strong_etag",
        source_generation='"version-a"',
        source_byte_length=100,
        source_mime_type="audio/wav",
        source_duration_ms=1000,
        source_authority_generation=2,
        source_trace_id=uuid4(),
        source_span_id="0123456789abcdef",
        source_direction="mixed",
        source_expires_at=NOW + timedelta(minutes=20),
        deadline=NOW + timedelta(minutes=10),
        policy_version=1,
        deletion_generation=0,
    )
    envelope = Row(
        creator_kind="user",
        creator_id=creator,
        generation_kind=job.source_generation_kind,
        generation=job.source_generation,
        source_bytes=100,
        duration_ms=1000,
        mime_type="audio/wav",
        authority_generation=2,
        trace_id=job.source_trace_id,
        span_id=job.source_span_id,
        direction="mixed",
        revoked_at=None,
        expires_at=NOW + timedelta(minutes=10),
        source_expires_at=job.source_expires_at,
    )
    return scope, session, job, envelope


@pytest.mark.parametrize(
    "field,value",
    [
        ("creator_id", UUID(int=8)),
        ("generation", '"same-size-replacement"'),
        ("authority_generation", 3),
        ("revoked_at", NOW),
        ("expires_at", NOW),
    ],
)
def test_source_fences_reject_creator_replacement_revocation_expiry(env, field, value):
    _, session, job, envelope = source_rows(env)
    env.u._check_source(job, session, (None, envelope, None))
    setattr(envelope, field, value)
    with pytest.raises(env.error):
        env.u._check_source(job, session, (None, envelope, None))


def test_importer_never_substitutes_session_identity(env):
    _, session, job, envelope = source_rows(env)
    job.importer_id = uuid4()
    with pytest.raises(env.error, match="source_unavailable"):
        env.u._check_source(job, session, (None, envelope, None))


def test_non_audio_probe_facts_are_absent_not_invented(env):
    _, asset, upload, _ = publication_upload(env)
    identity = env.i.ObjectIdentity(
        asset.storage_binding_id, "serving", upload.serving_key, "v1"
    )
    facts = env.v._facts(
        upload,
        identity,
        "a" * 64,
        {"mime_type": "image/png", "sample_rate": 0, "channels": 0, "frame_count": 0},
    )
    assert (facts.sample_rate, facts.channels, facts.frame_count) == (None, None, None)


def test_recording_segment_duration_is_bounded_before_ready(env):
    upload = Row(mime_type="audio/wav", recording_id=uuid4())
    with pytest.raises(env.error, match="validation_rejected"):
        env.v.accept(
            {
                "mime_type": "audio/wav",
                "sample_rate": 48000,
                "channels": 1,
                "frame_count": 48000 * 31,
            },
            upload,
        )


def test_no_removed_models_new_accounting_outboxes_or_default_storage_calls():
    for name in ("uploads", "accounting", "validation", "publication", "capacity_db"):
        tree = ast.parse((SERVICE / f"{name}.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert node.id not in {
                    "AttachmentBlob",
                    "UploadReservation",
                    "AttachmentUsageReceipt",
                }
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"create", "get_or_create"}
            ):
                if "AttachmentOutbox" in ast.unparse(node.func):
                    for kw in node.keywords:
                        if kw.arg == "event" and isinstance(kw.value, ast.Constant):
                            assert kw.value.value not in {"admitted", "settled"}
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "storage_from_settings"


def test_import_source_cannot_mint_client_upload_capability(env):
    for kind in ("RemoteUrl", "ProviderFile", "imported_recording"):
        with pytest.raises(env.error, match="permission_denied"):
            env.u.initialize(
                None, scope_id=uuid4(), body={"source_kind": kind}, key="synthetic"
            )


def test_empty_recovery_page_does_not_prove_put_termination(env):
    upload = Row(
        quarantine_key="quarantine/test",
        serving_key="original/test",
        write_deadline_at=NOW,
    )
    storage = Row(binding_id=uuid4())
    storage.list_versions = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("enumeration before prior write stopped")
    )
    with pytest.raises(env.error, match="transfer_conflict"):
        env.u._recover_key(
            upload, storage=storage, purpose="quarantine", guard=lambda: upload
        )


def test_empty_recovery_page_rechecks_epoch_after_io(env):
    upload = Row(
        quarantine_key="quarantine/test",
        serving_key="original/test",
        write_deadline_at=NOW - timedelta(seconds=16),
    )
    storage = Row(binding_id=uuid4())
    calls = []

    def guard():
        assert env.connection.in_atomic_block
        calls.append(1)
        if len(calls) == 2:
            raise env.error("validation_fenced", 409)
        return upload

    def page(*a, **k):
        assert not env.connection.in_atomic_block
        return env.i.VersionPage((), (), None)

    storage.list_versions = page
    with pytest.raises(env.error, match="validation_fenced"):
        env.u._recover_key(upload, storage=storage, purpose="quarantine", guard=guard)
    assert len(calls) == 2


@pytest.mark.parametrize("already_owned", [True, False])
def test_stale_result_can_only_delete_its_exact_unreferenced_version(
    env, monkeypatch, already_owned
):
    scope, asset, upload, _ = publication_upload(env)
    asset.verified_storage_key, asset.verified_storage_version = (
        upload.serving_key,
        "v-b",
    )
    upload.serving_version = "v-a" if already_owned else "v-b"
    upload.quarantine_key, upload.quarantine_version = "quarantine/server-id", "q1"
    identity = env.i.ObjectIdentity(
        asset.storage_binding_id, "serving", upload.serving_key, "v-a"
    )
    monkeypatch.setattr(
        env.u, "_lock_upload", lambda *a, **k: (scope, None, None, upload, asset, None)
    )
    erased = []

    def erase(identity):
        assert not env.connection.in_atomic_block
        erased.append(identity)

    storage = Row(erase_version=erase)
    result = env.u.cleanup_unowned_result(
        upload.id, scope_id=scope.id, identity=identity, storage=storage
    )
    assert result == ("already_owned" if already_owned else "stale_unowned")
    assert erased == ([] if already_owned else [identity])
    assert asset.verified_storage_version == "v-b"
    assert upload.reservation_settled_at is None


def test_source_retention_shrink_denies_original_job(env):
    _, session, job, envelope = source_rows(env)
    envelope.source_expires_at = NOW + timedelta(minutes=5)
    with pytest.raises(env.error, match="source_unavailable"):
        env.u._check_source(job, session, (None, envelope, None))


def test_unassigned_segment_uses_exact_scoped_client_and_plan(env):
    scope, asset, _, facts = publication_upload(env)
    asset.client_id = uuid4()
    job, _ = env.owner(
        scope,
        "import",
        state="validating",
        deadline=NOW + timedelta(minutes=10),
        lease_epoch=2,
        lease_phase="publishing",
        storage_binding_id=asset.storage_binding_id,
    )
    job.lease_token, job.lease_until = uuid4(), NOW + timedelta(minutes=5)
    fence = env.i.LeaseFence("import", job.id, job.lease_token, 2, job.lease_until)
    allocation = env.models["AttachmentRecordingSegment"].objects.create(
        scope_id=scope.id,
        state="staged",
        import_job_id=job.id,
        attachment_id=None,
        attachment_client_id=asset.client_id,
        storage_binding_id=asset.storage_binding_id,
        allocation_epoch=2,
        storage_key=facts.object.identity.key,
        storage_version="v1",
        sha256="a" * 64,
        byte_length=100,
        frame_count=48000,
    )
    with env.tx.atomic():
        env.p.publish_verified_locked(
            attachment=asset,
            facts=facts,
            expected_binding_id=asset.storage_binding_id,
            scope=scope,
            fence=fence,
            allocation=allocation,
        )
    assert asset.state == "ready"
    # Allocation linkage/state is the caller's atomic publication step.
    assert allocation.attachment_id is None


def test_normal_validation_keeps_snapshot_probe_promote_outside_locks(env, monkeypatch):
    import hashlib

    scope, asset, upload, _ = publication_upload(env)
    data = b"x" * 100
    digest = hashlib.sha256(data).hexdigest()
    upload.quarantine_key, upload.quarantine_version, upload.quarantine_sha256 = (
        "quarantine/server-id",
        "q1",
        digest,
    )
    upload.serving_version, upload.serving_write_attempts = None, 0
    upload.recording_id = None
    upload.cleanup_not_before = NOW + timedelta(hours=1)
    upload.quarantine_write_attempts, upload.usage_outcome = 1, None
    upload.settlement_event_id = None
    upload.reservation_decoded_bytes = 1024
    claim = Row(id=uuid4(), attempts=1, available_at=NOW + timedelta(seconds=60))
    calls = []

    def snapshot(identity, output, *, expected_length):
        assert not env.connection.in_atomic_block
        assert identity.version == "q1" and expected_length == 100
        calls.append("snapshot")
        output.write(data)
        return digest

    def probe(path):
        assert not env.connection.in_atomic_block
        calls.append("probe")
        return {
            "mime_type": "audio/wav",
            "sample_rate": 48000,
            "channels": 1,
            "frame_count": 48000,
        }

    def promote(snapshot, *, permit, expected_length, expected_sha256):
        assert not env.connection.in_atomic_block
        assert (
            permit.write_attempt == 1
            and expected_length == 100
            and expected_sha256 == digest
        )
        calls.append("promote")
        return env.i.ObjectIdentity(
            asset.storage_binding_id, "serving", upload.serving_key, "v2"
        )

    storage = Row(
        binding_id=asset.storage_binding_id, snapshot=snapshot, promote=promote
    )

    def guard(*args):
        assert env.connection.in_atomic_block
        return scope, None, None, upload, asset, None

    monkeypatch.setattr(env.v, "_guard", guard)
    monkeypatch.setattr(env.v, "_check", lambda upload: None)
    monkeypatch.setattr(env.v, "_storage_for", lambda *a, **k: storage)
    monkeypatch.setattr(env.v, "sandbox_probe", probe)
    monkeypatch.setattr(env.v, "_cleanup_ready", lambda *a: False)
    readiness = types.ModuleType("tracer.services.attachments.readiness")
    readiness.require_decoder = lambda requirements: None
    monkeypatch.setitem(sys.modules, readiness.__name__, readiness)
    derivatives = types.ModuleType("tracer.services.attachments.derivatives")

    def enqueue(attachment):
        assert env.connection.in_atomic_block
        assert attachment.state == "ready"
        calls.append("derivatives")

    derivatives.enqueue_for_asset_locked = enqueue
    monkeypatch.setitem(sys.modules, derivatives.__name__, derivatives)
    facts = env.v.validate(
        upload.id, scope_id=scope.id, storage=storage, _claim_event=claim
    )
    assert calls == ["snapshot", "probe", "promote", "derivatives"]
    assert facts.object.sha256 == digest
    assert (upload.state, upload.usage_outcome, upload.usage_stored_bytes) == (
        "ready",
        "ready",
        100,
    )
    assert upload.reservation_settled_at is None  # accepted horizon still charged


@pytest.mark.parametrize("bad", ["length", "digest", "recorded_digest"])
def test_snapshot_rehashes_exact_bytes_before_decoder(env, monkeypatch, tmp_path, bad):
    import hashlib

    scope, asset, upload, _ = publication_upload(env)
    data = b"x" * 100
    digest = hashlib.sha256(data).hexdigest()
    upload.quarantine_key, upload.quarantine_version, upload.quarantine_sha256 = (
        "quarantine/server-id",
        "q1",
        digest,
    )
    upload.reservation_decoded_bytes = 1024
    if bad == "recorded_digest":
        upload.quarantine_sha256 = "0" * 64

    def snapshot(identity, output, **kwargs):
        assert not env.connection.in_atomic_block
        output.write(data[:-1] if bad == "length" else data)
        return "0" * 64 if bad == "digest" else digest

    storage = Row(binding_id=asset.storage_binding_id, snapshot=snapshot)
    readiness = types.ModuleType("tracer.services.attachments.readiness")
    readiness.require_decoder = lambda requirements: None
    monkeypatch.setitem(sys.modules, readiness.__name__, readiness)
    monkeypatch.setattr(env.v, "_check", lambda upload: None)
    monkeypatch.setattr(
        env.v,
        "sandbox_probe",
        lambda path: (_ for _ in ()).throw(
            AssertionError("probe on unverified snapshot")
        ),
    )
    with (
        (tmp_path / "snapshot").open("w+b") as output,
        pytest.raises(env.error, match="snapshot_changed"),
    ):
        env.v._snapshot(upload, storage, output)


def test_abort_rechecks_complete_winner_under_locks(env, monkeypatch):
    scope = env.scope()
    ref, _ = env.owner(scope, state="pending_upload")
    current = Row(**{**ref.__dict__, "state": "validating"})
    monkeypatch.setattr(env.u, "_live_upload", lambda *a, **k: ref)
    monkeypatch.setattr(
        env.u, "_lock_upload", lambda *a, **k: (scope, None, None, current, None, None)
    )
    monkeypatch.setattr(
        env.u,
        "settle",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("abort after complete")),
    )
    with pytest.raises(env.error, match="transfer_conflict"):
        env.u.abort("synthetic-token", scope_id=scope.id, upload_id=ref.id)
    assert current.state == "validating"


def test_claim_uses_locked_upload_state_for_exhaustion():
    tree = ast.parse((SERVICE / "validation.py").read_text())
    claim = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_claim"
    )
    assignments = [
        node
        for node in ast.walk(claim)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "upload"
            for target in node.targets
        )
    ]
    assert any("select_for_update" in ast.unparse(node.value) for node in assignments)
