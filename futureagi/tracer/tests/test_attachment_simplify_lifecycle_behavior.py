"""Database-free execution of the actual lifecycle functions with narrow IO doubles.

No Django app, SQL trigger, CH, Redis or native storage qualification is claimed.
The AST loader only replaces import-time infrastructure; function bodies, branches
and frozen storage values are the actual committed production source.
"""

import ast
import dataclasses
import importlib.util
import sys
from collections import defaultdict
from contextlib import ContextDecorator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest
from django.db.models import Q

SERVICES = Path(__file__).resolve().parents[1] / "services" / "attachments"
NOW = datetime(2026, 11, 16, 22, tzinfo=UTC)


class Error(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


def load(name, names, **values):
    tree = ast.parse((SERVICES / f"{name}.py").read_text())
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
    ]
    assert {node.name for node in nodes} == set(names)
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            *nodes,
        ],
        type_ignores=[],
    )
    env = {
        "__name__": __name__,
        "UUID": UUID,
        "timedelta": timedelta,
        "datetime": datetime,
        "UTC": UTC,
        "Q": Q,
        "AttachmentError": Error,
        "timezone": NS(
            now=lambda: NOW,
            is_naive=lambda v: v.tzinfo is None,
            is_aware=lambda v: v.tzinfo is not None,
        ),
        **values,
    }
    exec(
        compile(
            ast.fix_missing_locations(module), str(SERVICES / f"{name}.py"), "exec"
        ),
        env,
    )
    return env


@pytest.fixture
def interfaces(monkeypatch):
    name = "_lifecycle_test_interfaces"
    spec = importlib.util.spec_from_file_location(name, SERVICES / "interfaces.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def module(monkeypatch, name, **attrs):
    value = ModuleType(name)
    value.__dict__.update(attrs)
    value.__path__ = []
    monkeypatch.setitem(sys.modules, name, value)
    if "." in name:
        parent, attr = name.rsplit(".", 1)
        if parent not in sys.modules:
            module(monkeypatch, parent)
        monkeypatch.setattr(sys.modules[parent], attr, value, raising=False)
    return value


class Row(NS):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not hasattr(self, "pk"):
            self.pk = uuid4()
        self.id = self.pk
        self.saves = []

    def save(self, *, update_fields):
        self.saves.append(tuple(update_fields))


def matches(row, key, value):
    parts = key.split("__")
    op = (
        parts.pop()
        if parts[-1] in {"in", "isnull", "gt", "gte", "lte", "lt"}
        else "exact"
    )
    actual = row
    for part in parts:
        actual = getattr(actual, part)
    if op == "exact":
        return actual == value
    if op == "in":
        return actual in value
    if op == "isnull":
        return (actual is None) == value
    if actual is None:
        return False
    return {
        "gt": lambda: actual > value,
        "gte": lambda: actual >= value,
        "lt": lambda: actual < value,
        "lte": lambda: actual <= value,
    }[op]()


def condition(row, q):
    answers = [
        condition(row, child) if isinstance(child, Q) else matches(row, *child)
        for child in q.children
    ]
    answer = any(answers) if q.connector == "OR" else all(answers)
    return not answer if q.negated else answer


class Query:
    def __init__(self, rows=(), *, log=None, kind="row"):
        self.rows = list(rows)
        self.log = log if log is not None else []
        self.kind = kind

    def filter(self, *conditions, **kwargs):
        return Query(
            [
                row
                for row in self.rows
                if all(condition(row, q) for q in conditions)
                and all(matches(row, key, value) for key, value in kwargs.items())
            ],
            log=self.log,
            kind=self.kind,
        )

    def select_for_update(self, **kwargs):
        self.log.append(self.kind)
        return self

    def order_by(self, *fields):
        return self

    def values_list(self, name, flat=False):
        assert flat
        return [getattr(row, name) for row in self.rows]

    def get(self, **kwargs):
        rows = self.filter(**kwargs).rows
        assert len(rows) == 1
        return rows[0]

    def exists(self):
        return bool(self.rows)

    def update(self, **kwargs):
        for row in self.rows:
            row.__dict__.update(kwargs)
        return len(self.rows)

    def iterator(self, **kwargs):
        return iter(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def __getitem__(self, item):
        return self.rows[item]


class Atomic(ContextDecorator):
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.in_atomic_block += 1

    def __exit__(self, *exc):
        self.connection.in_atomic_block -= 1


@pytest.fixture
def cleanup(monkeypatch, interfaces):
    scope = Row(deletion_generation=2)
    asset = Row(
        scope=scope,
        scope_id=scope.pk,
        client_id=uuid4(),
        storage_binding_id=uuid4(),
        deleted_at=NOW - timedelta(hours=2),
        state="deleted",
        verified_storage_key="original/kept-historical",
        verified_storage_version="original-v1",
    )
    upload = Row(
        scope=scope,
        scope_id=scope.pk,
        attachment_id=asset.pk,
        allocation_epoch=1,
        state="ready",
        quarantine_key="quarantine/upload-server-id",
        quarantine_version="q-v1",
        serving_key="original/kept-historical",
        serving_version="original-v1",
        cleanup_not_before=NOW - timedelta(minutes=30),
        write_deadline_at=NOW - timedelta(hours=1),
        expires_at=NOW - timedelta(hours=1),
        quarantine_write_attempts=1,
        serving_write_attempts=1,
        transfer_started_at=None,
        cleanup_checked_at=None,
        unowned_cleanup_due_at=NOW,
    )
    derivative = Row(
        state="ready",
        lease_epoch=1,
        storage_key="original/server-derivative-id",
        storage_version="d-v1",
        quarantine_key="quarantine/server-derivative-id",
        cleanup_not_before=NOW - timedelta(minutes=30),
        write_deadline_at=None,
        write_attempts=1,
        lease_token=None,
        lease_until=None,
    )
    chain = [scope, asset, [upload], [], [], [derivative]]
    connection = NS(in_atomic_block=0)
    transaction = NS(atomic=lambda: Atomic(connection))
    calls = []
    values = {
        "AttachmentState": NS(DELETED="deleted"),
        "connection": connection,
        "transaction": transaction,
        "ATTACHMENT_ERASE_HORIZON": timedelta(minutes=15, seconds=15),
        "TRANSFER_DRAIN": timedelta(seconds=75),
        "ObjectLocator": interfaces.ObjectLocator,
        "EraseResult": interfaces.EraseResult,
        "AttachmentOutbox": NS(objects=Query()),
        "AttachmentScope": NS(objects=Query([scope])),
        "AttachmentUpload": NS(objects=Query([upload])),
    }
    names = {
        "_cleanup_plan",
        "_writers_stopped",
        "_erase_originals",
        "_erase_asset",
        "erase_asset",
        "erase",
    }
    env = load("retention", names, **values)
    env["_lock_asset_chain"] = lambda **kw: tuple(chain)
    env["_survivors"] = lambda *args: Query()

    class Storage:
        binding_id = asset.storage_binding_id

        def verify_identity(self, *, operation):
            assert operation == "erase"
            assert not connection.in_atomic_block
            calls.append("verify")

        def erase_all_versions(self, locator):
            assert not connection.in_atomic_block
            assert asset.deleted_at and asset.state == "deleted"
            assert locator.binding_id == self.binding_id
            calls.append(locator)
            return interfaces.EraseResult(True, False, None, NOW)

    storage = Storage()

    def resolve(binding_id, *, operation):
        assert binding_id == asset.storage_binding_id and operation == "erase"
        calls.append("resolve-A")
        return storage

    def erase_derivatives(**kwargs):
        assert not connection.in_atomic_block
        assert asset.deleted_at
        calls.append("derivatives")
        for row in chain[5]:
            row.state = "erased"
        return True

    def settle(*args, **kwargs):
        assert not connection.in_atomic_block
        calls.append("settle")
        return True

    module(
        monkeypatch, "tracer.services.attachments.readiness", resolve_historical=resolve
    )
    derivatives = module(
        monkeypatch,
        "tracer.services.attachments.derivatives",
        erase_for_asset=erase_derivatives,
    )
    module(monkeypatch, "tracer.services.attachments.uploads", settle=settle)
    return NS(
        env=env,
        chain=chain,
        scope=scope,
        asset=asset,
        upload=upload,
        derivative=derivative,
        storage=storage,
        calls=calls,
        connection=connection,
        derivatives=derivatives,
        interfaces=interfaces,
    )


def run_erase(ctx):
    return ctx.env["erase_asset"](
        attachment_id=ctx.asset.pk, scope_id=ctx.scope.pk, now=NOW
    )


def test_b3_uploaded_asset_erases_original_quarantine_and_all_representations(cleanup):
    assert run_erase(cleanup)
    locators = [
        value
        for value in cleanup.calls
        if isinstance(value, cleanup.interfaces.ObjectLocator)
    ]
    assert {(loc.purpose, loc.key) for loc in locators} == {
        ("serving", cleanup.upload.serving_key),
        ("quarantine", cleanup.upload.quarantine_key),
    }
    assert cleanup.calls.index("derivatives") < cleanup.calls.index("settle")
    assert cleanup.upload.cleanup_checked_at == NOW
    assert cleanup.derivative.state == "erased"


def test_b4_historical_a_cleanup_does_not_resolve_invalid_default_b(
    cleanup, monkeypatch
):
    readiness = sys.modules["tracer.services.attachments.readiness"]
    monkeypatch.setattr(
        readiness,
        "require_new_admission",
        lambda **kw: pytest.fail("default B accessed"),
        raising=False,
    )
    monkeypatch.setattr(
        readiness,
        "require_decoder",
        lambda *a: pytest.fail("decoder accessed"),
        raising=False,
    )
    monkeypatch.setattr(
        readiness,
        "require_source_keys",
        lambda **kw: pytest.fail("source keys accessed"),
        raising=False,
    )
    assert run_erase(cleanup)
    assert cleanup.calls[0] == "resolve-A"


def test_survivor_or_unknown_parent_prevents_all_storage(cleanup):
    cleanup.env["_survivors"] = lambda *args: Query([Row()])
    assert not run_erase(cleanup)
    assert cleanup.calls == []
    assert cleanup.upload.state == "ready"


def test_tombstone_commits_before_unlocked_storage(cleanup):
    cleanup.asset.deleted_at = None
    cleanup.asset.state = "ready"
    assert not run_erase(cleanup)  # newly created fence still has a drain horizon
    assert cleanup.asset.deleted_at == NOW
    assert "derivatives" in cleanup.calls and "settle" not in cleanup.calls


def test_nested_transaction_cannot_claim_fence_committed(cleanup):
    cleanup.connection.in_atomic_block = 1
    with pytest.raises(Error, match="cleanup_fenced"):
        run_erase(cleanup)
    assert cleanup.calls == []


@pytest.mark.parametrize(
    "result", ["partial", "retryable", "reason", "early", "future", "bool", "none"]
)
def test_ambiguous_or_early_erase_never_releases_capacity(cleanup, result):
    values = {
        "partial": cleanup.interfaces.EraseResult(False, True, "pending", NOW),
        "retryable": cleanup.interfaces.EraseResult(True, True, None, NOW),
        "reason": cleanup.interfaces.EraseResult(True, False, "held", NOW),
        "early": cleanup.interfaces.EraseResult(
            True, False, None, NOW - timedelta(days=1)
        ),
        "future": cleanup.interfaces.EraseResult(
            True, False, None, NOW + timedelta(seconds=1)
        ),
        "bool": True,
        "none": None,
    }
    cleanup.storage.erase_all_versions = lambda locator: values[result]
    assert not run_erase(cleanup)
    assert "settle" not in cleanup.calls
    assert cleanup.upload.cleanup_checked_at is None


def test_injected_storage_must_match_original_binding(cleanup):
    cleanup.storage.binding_id = uuid4()
    with pytest.raises(Error, match="storage_unverified"):
        cleanup.env["_erase_asset"](
            attachment_id=cleanup.asset.pk,
            scope_id=cleanup.scope.pk,
            now=NOW,
            storage=cleanup.storage,
        )
    assert cleanup.calls == []


@pytest.mark.parametrize(
    "change", ["generation", "horizon", "epoch", "locator", "survivor"]
)
def test_completion_rechecks_generation_horizon_epoch_locators_survivors(
    cleanup, change
):
    erase = cleanup.derivatives.erase_for_asset

    def race(**kwargs):
        erase(**kwargs)
        if change == "generation":
            cleanup.scope.deletion_generation += 1
        elif change == "horizon":
            cleanup.upload.cleanup_not_before = NOW + timedelta(hours=1)
        elif change == "epoch":
            cleanup.upload.allocation_epoch += 1
        elif change == "locator":
            cleanup.upload.serving_key = "original/different"
        else:
            cleanup.env["_survivors"] = lambda *args: Query([Row()])
        return True

    cleanup.derivatives.erase_for_asset = race
    assert not run_erase(cleanup)
    assert cleanup.upload.cleanup_checked_at is None
    assert "settle" not in cleanup.calls


@pytest.mark.parametrize(
    "live_writer", ["upload", "import", "derivative", "validation"]
)
def test_clock_expiry_is_not_accepted_write_termination(cleanup, live_writer):
    if live_writer == "upload":
        cleanup.upload.transfer_started_at = NOW - timedelta(days=2)
    elif live_writer == "derivative":
        cleanup.derivative.lease_token = uuid4()
    elif live_writer == "import":
        cleanup.chain[3].append(
            Row(
                storage_binding_id=cleanup.asset.storage_binding_id,
                cleanup_not_before=NOW - timedelta(days=1),
                lease_epoch=1,
                lease_token=uuid4(),
                lease_until=NOW - timedelta(hours=1),
                lease_phase="writing",
            )
        )
    else:
        cleanup.env["AttachmentOutbox"].objects = Query(
            [
                Row(
                    upload_id=cleanup.upload.pk,
                    event="validate",
                    processed_at=None,
                    attempts=1,
                )
            ]
        )
    assert not run_erase(cleanup)
    assert "settle" not in cleanup.calls


def test_import_only_asset_includes_linked_staging_locators(cleanup):
    cleanup.chain[2] = []
    segment = Row(
        storage_binding_id=cleanup.asset.storage_binding_id,
        attachment_id=cleanup.asset.pk,
        import_job_id=uuid4(),
        allocation_epoch=2,
        quarantine_key="quarantine/staged-server-id",
        storage_key="original/staged-server-id",
        storage_version="s-v2",
        cleanup_not_before=NOW - timedelta(minutes=30),
        write_deadline_at=None,
        write_attempts=2,
        state="published",
        erased_at=None,
    )
    cleanup.chain[4] = [segment]
    assert run_erase(cleanup)
    assert segment.state == "erased" and segment.erased_at == NOW
    assert {
        loc.key
        for loc in cleanup.calls
        if isinstance(loc, cleanup.interfaces.ObjectLocator)
    } == {
        "original/kept-historical",
        "original/staged-server-id",
        "quarantine/staged-server-id",
    }
    assert "derivatives" in cleanup.calls


def test_import_allocation_foreign_binding_is_never_guessed(cleanup):
    cleanup.chain[4] = [Row(storage_binding_id=uuid4(), quarantine_key="quarantine/x")]
    with pytest.raises(Error, match="storage_unverified"):
        run_erase(cleanup)
    assert cleanup.calls == []


@pytest.mark.parametrize("owner", ["asset", "upload", "derivative"])
def test_unresolved_provenance_or_horizon_stays_pending(cleanup, owner):
    if owner == "asset":
        cleanup.asset.storage_binding_id = None
    elif owner == "upload":
        cleanup.upload.serving_key = None
    else:
        cleanup.derivative.cleanup_not_before = None
    with pytest.raises(Error):
        run_erase(cleanup)
    assert cleanup.calls == []


@pytest.mark.parametrize("upload_owned", [False, True])
@pytest.mark.parametrize("complete", [False, True])
def test_outbox_done_means_terminal_not_attempt(cleanup, upload_owned, complete):
    event = Row(
        event="erase",
        scope_id=cleanup.scope.pk,
        upload_id=cleanup.upload.pk if upload_owned else None,
        attachment_id=cleanup.asset.pk,
        generation=1,
        processed_at=None,
        available_at=NOW - timedelta(seconds=1),
        attempts=0,
    )
    cleanup.env["AttachmentOutbox"].objects = Query([event])
    if not complete:
        cleanup.derivatives.erase_for_asset = lambda **kwargs: False
    assert cleanup.env["erase"](event.pk) is complete
    assert event.attempts == 1
    assert (event.processed_at is not None) is complete
    if not complete:
        assert event.available_at > NOW and "settle" not in cleanup.calls
    else:
        before = list(cleanup.calls)
        assert not cleanup.env["erase"](event.pk)
        assert cleanup.calls == before


def test_outbox_generation_change_after_cleanup_remains_pending(cleanup):
    event = Row(
        event="erase",
        scope_id=cleanup.scope.pk,
        upload_id=None,
        attachment_id=cleanup.asset.pk,
        generation=1,
        processed_at=None,
        available_at=NOW,
        attempts=0,
    )
    cleanup.env["AttachmentOutbox"].objects = Query([event])
    cleanup.upload.cleanup_checked_at = NOW
    cleanup.derivative.state = "erased"

    def erased(**kwargs):
        cleanup.scope.deletion_generation += 1
        return True

    cleanup.env["_erase_asset"] = erased
    assert not cleanup.env["erase"](event.pk)
    assert event.processed_at is None


@pytest.mark.parametrize(
    "clock", ["occurrence", "source", "deleted", "generation", "unknown", "live"]
)
def test_survivor_expiry_is_independent_and_unknown_authority_is_retained(clock):
    scope = Row(deletion_generation=2)
    asset_id = uuid4()
    row = Row(
        scope=scope,
        attachment_id=asset_id,
        deleted_at=None,
        expires_at=NOW + timedelta(days=1),
        source_expires_at=NOW + timedelta(hours=1),
        deletion_generation=2,
    )
    if clock == "occurrence":
        row.expires_at = NOW
    elif clock == "source":
        row.source_expires_at = NOW
    elif clock == "deleted":
        row.deleted_at = NOW
    elif clock == "generation":
        row.deletion_generation = 1
    elif clock == "unknown":
        row.source_expires_at = None
    env = load(
        "retention", {"_survivors"}, AttachmentOccurrence=NS(objects=Query([row]))
    )
    assert env["_survivors"](scope, [asset_id], NOW).exists() is (
        clock in {"live", "unknown"}
    )


@pytest.mark.parametrize("order", [("deleted", "live"), ("live", "deleted")])
def test_batch_tombstones_do_not_erase_survivor_in_either_order(order):
    scope = Row(deletion_generation=1)
    asset = Row(scope=scope, deleted_at=None, state="ready")
    rows = {
        name: Row(
            scope=scope,
            attachment_id=asset.pk,
            deleted_at=None,
            expires_at=NOW + timedelta(days=1),
            source_expires_at=NOW + timedelta(days=1),
            deletion_generation=1,
        )
        for name in order
    }
    rows["deleted"].expires_at = NOW
    locks, queued, audited = [], [], []
    env = load(
        "retention",
        {"_survivors", "_tombstone_rows"},
        Attachment=NS(objects=Query([asset], log=locks, kind="asset")),
        AttachmentOccurrence=NS(
            objects=Query(rows.values(), log=locks, kind="occurrence")
        ),
        AttachmentState=NS(DELETED="deleted"),
        audit=lambda **kwargs: audited.append(kwargs),
    )
    env["_queue_asset_erase"] = lambda *args: queued.append(args)
    assert env["_tombstone_rows"](scope, [rows["deleted"]], reason="retention") == 1
    assert asset.deleted_at is None and not queued
    assert locks == ["asset", "occurrence"]
    assert env["_tombstone_rows"](scope, [rows["live"]], reason="requested") == 1
    assert asset.state == "deleted" and len(queued) == 1
    assert len(audited) == 2


def test_orphan_live_reset_wins_locked_recheck():
    scope = Row(deletion_generation=1)
    asset = Row(scope=scope, deleted_at=None, state="ready")
    row = Row(
        scope=scope,
        attachment_id=asset.pk,
        deleted_at=None,
        bound_at=NOW - timedelta(days=3),
        source_missing_since=None,
        expires_at=NOW + timedelta(days=1),
        source_expires_at=NOW + timedelta(days=1),
        deletion_generation=1,
    )
    env = load(
        "retention",
        {"_survivors", "_tombstone_rows"},
        Attachment=NS(objects=Query([asset])),
        AttachmentOccurrence=NS(objects=Query([row])),
        AttachmentState=NS(DELETED="deleted"),
        audit=lambda **kwargs: pytest.fail("audit"),
    )
    env["_queue_asset_erase"] = lambda *args: pytest.fail("queued")
    assert (
        env["_tombstone_rows"](
            scope,
            [row],
            reason="retention",
            eligible=Q(source_missing_since__lte=NOW - timedelta(days=1)),
        )
        == 0
    )
    assert row.deleted_at is None and asset.deleted_at is None


@pytest.mark.parametrize("disabled", [False, True])
def test_drain_selects_each_owner_before_resolution_and_isolates_bad_binding(disabled):
    calls, pushed = [], []

    def consume(pk, storage):
        assert storage is None
        calls.append(pk)
        if pk == "bad-B":
            raise Error("storage_unverified", 503)
        return True

    env = load(
        "lifecycle",
        {"drain_outbox"},
        settings=NS(FI_ATTACHMENTS_ENABLED=not disabled),
        DRAIN_LIMITS={"erase": 16},
        CONSUMERS={"erase": consume},
        defaultdict=defaultdict,
        RETRY_SECONDS=60,
        NOT_YET_SECONDS=300,
        logger=NS(exception=lambda *a, **k: None),
    )
    env["_due"] = lambda kind, limit: ["bad-B", "valid-A"]
    env["_push_back"] = lambda *args: pushed.append(args)
    result = env["drain_outbox"]()
    assert result == (
        {} if disabled else {"erase_error_storage_unverified": 1, "erase_done": 1}
    )
    assert calls == ([] if disabled else ["bad-B", "valid-A"])


@pytest.fixture
def authority(monkeypatch):
    parent_env = load(
        "occurrences", {"Parent", "SourceState"}, dataclass=dataclasses.dataclass
    )
    calls = []
    deleted = set()

    def deleted_session_ids(project, sessions, reader):
        calls.append((project, sessions))
        return deleted

    module(
        monkeypatch,
        "tracer.services.attachments.session_deletion",
        deleted_sessions=lambda p: (NS(exists=lambda: True), NS(exists=lambda: False)),
        deleted_session_ids=deleted_session_ids,
    )
    module(monkeypatch, "tracer.services.clickhouse.v2", get_reader=lambda: object())
    env = load(
        "authority",
        {"_aware", "source_state", "BatchSessionDeletion", "authorize_parent_batch"},
        SourceState=parent_env["SourceState"],
        SOURCE_GENERATION=0,
        retention_for=lambda org, start: start + timedelta(days=3),
        logger=NS(warning=lambda *a, **kw: None),
    )
    scope = Row(project_id=uuid4(), organization_id=uuid4())
    parents = [
        parent_env["Parent"](uuid4(), "0000000000000001"),
        parent_env["Parent"](uuid4(), "0000000000000002"),
    ]
    session_ids = [uuid4(), uuid4()]
    spans = {
        (str(parent.trace_id), parent.span_id): NS(
            project_id=scope.project_id,
            trace_id=parent.trace_id,
            id=parent.span_id,
            start_time=NOW - timedelta(hours=1),
            is_deleted=False,
            trace_session_id=session,
        )
        for parent, session in zip(parents, session_ids, strict=True)
    }
    deleted.add(str(session_ids[0]))
    return NS(env=env, scope=scope, parents=parents, spans=spans, calls=calls)


@pytest.mark.parametrize("reverse", [False, True])
def test_batched_committed_marker_denies_deleted_keeps_live_both_orders(
    authority, reverse
):
    parents = (
        tuple(reversed(authority.parents)) if reverse else tuple(authority.parents)
    )
    result = authority.env["authorize_parent_batch"](
        scope=authority.scope,
        parents=parents,
        spans=authority.spans,
        retention=lambda org, start: start + timedelta(days=1),
    )
    dead, live = authority.parents
    assert result[(dead.trace_id, dead.span_id)] is None
    assert result[(live.trace_id, live.span_id)].readable
    assert len(authority.calls) == 1


@pytest.mark.parametrize(
    "failure", ["marker", "retention", "scope", "trace", "span", "malformed-session"]
)
def test_parent_authority_failure_never_grants_batch_entry(
    authority, monkeypatch, failure
):
    parent = authority.parents[1]
    span = authority.spans[(str(parent.trace_id), parent.span_id)]

    def retention(org, start):
        return start + timedelta(days=1)

    if failure == "marker":
        monkeypatch.setattr(
            sys.modules["tracer.services.attachments.session_deletion"],
            "deleted_session_ids",
            lambda *args: (_ for _ in ()).throw(RuntimeError("offline")),
        )
    elif failure == "retention":

        def retention(*args):
            return (_ for _ in ()).throw(RuntimeError("offline"))
    elif failure == "scope":
        span.project_id = uuid4()
    elif failure == "trace":
        span.trace_id = uuid4()
    elif failure == "span":
        span.id = "000000000000000f"
    else:
        span.trace_session_id = "not-a-session"
    result = authority.env["authorize_parent_batch"](
        scope=authority.scope,
        parents=(parent,),
        spans=authority.spans,
        retention=retention,
    )
    assert result[(parent.trace_id, parent.span_id)] is None


def test_batches_do_not_cache_a_previous_operation(authority, monkeypatch):
    parent = authority.parents[1]
    kwargs = {
        "scope": authority.scope,
        "parents": (parent,),
        "spans": authority.spans,
        "retention": lambda org, start: start + timedelta(days=1),
    }
    assert authority.env["authorize_parent_batch"](**kwargs)[
        (parent.trace_id, parent.span_id)
    ]
    monkeypatch.setattr(
        sys.modules["tracer.services.attachments.session_deletion"],
        "deleted_session_ids",
        lambda *args: {
            str(
                authority.spans[(str(parent.trace_id), parent.span_id)].trace_session_id
            )
        },
    )
    assert (
        authority.env["authorize_parent_batch"](**kwargs)[
            (parent.trace_id, parent.span_id)
        ]
        is None
    )


@pytest.mark.parametrize(
    "failure", ["deleted", "source-expired", "revoked", "scope-generation"]
)
def test_late_binding_rejects_tombstones_source_expiry_and_revocation(failure):
    connection = NS(in_atomic_block=0)

    class Transaction:
        def atomic(self, fn=None):
            atomic = Atomic(connection)
            return atomic if fn is None else atomic(fn)

    scope = Row(enabled=True, deletion_generation=1, policy_version=1)
    session = Row(scope=scope, revoked_at=None)
    asset = Row(
        scope=scope,
        deleted_at=None,
        state="ready",
        deletion_generation=1,
        policy_version=1,
    )
    row = Row(
        scope=scope,
        capture_session_id=session.pk,
        attachment_id=asset.pk,
        deleted_at=None,
        expires_at=NOW + timedelta(days=1),
        deletion_generation=1,
        policy_version=1,
        trace_id=uuid4(),
        span_id="0000000000000001",
        bound_at=None,
    )
    source = NS(
        scope_id=scope.pk,
        parent=NS(trace_id=row.trace_id, span_id=row.span_id),
        generation=0,
        started_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(days=2),
    )
    if failure == "deleted":
        asset.deleted_at = NOW
    elif failure == "source-expired":
        source.expires_at = NOW
    elif failure == "revoked":
        session.revoked_at = NOW
    else:
        scope.deletion_generation += 1
    env = load(
        "retention",
        {"bind_retention"},
        transaction=Transaction(),
        AttachmentScope=NS(objects=Query([scope])),
        CaptureSession=NS(objects=Query([session])),
        Attachment=NS(objects=Query([asset])),
        AttachmentOccurrence=NS(objects=Query([row])),
        AttachmentState=NS(DELETED="deleted"),
        audit=lambda **kwargs: pytest.fail("audit"),
    )
    with pytest.raises(Error, match="binding_fenced"):
        env["bind_retention"](scope_id=scope.pk, occurrence_id=row.pk, source=source)
    assert row.bound_at is None


def test_no_removed_models_default_construction_or_new_accounting_outboxes():
    for name in (
        "authority",
        "occurrences",
        "control_plane",
        "binding",
        "retention",
        "session_deletion",
        "lifecycle",
        "wire",
        "bootstrap",
    ):
        text = (SERVICES / f"{name}.py").read_text()
        for removed in (
            "AttachmentBlob",
            "UploadReservation",
            "AttachmentUsageReceipt",
        ):
            assert removed not in text
        assert "from_settings(" not in text
    tree = ast.parse((SERVICES / "retention.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.keyword)
            and node.arg == "event"
            and isinstance(node.value, ast.Constant)
        ):
            assert node.value.value not in {"admitted", "settled"}


def test_startup_keeps_shared_cache_guard_without_any_storage_construction():
    source = (SERVICES.parents[1] / "apps.py").read_text()
    assert "verify_admission_cache(settings)" in source
    assert 'getattr(settings, "FI_ATTACHMENTS_ENABLED", False)' in source
    assert "from_settings" not in source and "verify_readiness" not in source


def test_control_plane_projection_and_shared_batch_authorization_are_preserved():
    tree = ast.parse((SERVICES / "control_plane.py").read_text())
    columns = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "SPAN_COLUMNS" for t in node.targets
        )
    )
    assert "trace_session_id" in columns
    text = ast.unparse(tree)
    assert "authorize_parent_batch(" in text
    assert "verified_frame_count" in text
    assert "attachmentblob" not in text
