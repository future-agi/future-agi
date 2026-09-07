"""Projectless contracts with in-memory IO doubles; no service is started."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2.property_catalog import dev_runtime as runtime
from tracer.services.clickhouse.v2.property_catalog.activation import RevisionBuildPlan
from tracer.services.clickhouse.v2.property_catalog.codec import canonical_json
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    DurableLifecycleError,
    LifecycleRunMode,
    ReservationStatus,
)
from tracer.services.clickhouse.v2.property_catalog.revision_fence_registry import (
    RevisionFenceRegistryError,
    decode_revision_fence_registry,
    encode_revision_fence_registry,
)
from tracer.services.clickhouse.v2.property_catalog.span_source import (
    _EMPTY_FENCE_SQL,
    AuthoritativeSpanReconciler,
    AuthoritativeSpanRole,
    CanonicalSpanAttributeGroupPageLoader,
    PropertyCatalogSpanSourceError,
    RevisionPinnedSpanAttributeGroupPageLoader,
    SpanAuditAccumulator,
    SpanScanCursor,
)
from tracer.tests import test_property_catalog_dev_rollout as dev
from tracer.tests import test_property_catalog_durable_lifecycle as lifecycle
from tracer.tests import test_property_catalog_reconciler as reconcile
from tracer.tests import test_property_catalog_revision_fence_registry as fences
from tracer.tests import test_property_catalog_span_source as spans
from tracer.tests import test_unified_property_catalog_reader as definitions
from tracer.tests import test_unified_property_catalog_value_reader as values
from tracer.tests.test_property_catalog_managed_runtime_activation import Driver


def _empty_inventory():
    return runtime.PostgresWorkspaceProjectInventory(dev.ORG, dev.WORKSPACE, False, ())


def _authorize(project_ids, inventory):
    return runtime._authorize_project_tenant_bindings(
        request=dev._request(execute=True),
        config=replace(dev._unit_runtime_config(), project_ids=project_ids),
        observation=dev._provenance_observation(),
        bindings=inventory,
        authorized_at=dev.ATTESTED_AT,
    )


def test_empty_authorization_requires_positive_active_workspace_witness():
    proof = _authorize((), _empty_inventory())
    assert proof.project_ids == () and proof.as_dict()["project_count"] == 0
    for invalid in (
        None,
        (),
        [],
        replace(_empty_inventory(), workspace_id=dev.OTHER_WORKSPACE),
    ):
        with pytest.raises(runtime.PropertyCatalogDevRuntimeError):
            _authorize((), invalid)


@pytest.mark.parametrize(
    "requested,observed",
    [
        ((), (dev.PROJECT,)),
        ((dev.PROJECT,), ()),
        ((dev.OTHER_PROJECT,), (dev.PROJECT,)),
    ],
)
def test_authorization_rejects_added_removed_or_foreign_requested_projects(
    requested, observed
):
    with pytest.raises(runtime.PropertyCatalogDevRuntimeError, match="missing exact"):
        _authorize(requested, dev._project_bindings(observed))


def test_default_workspace_transition_changes_authorization_digest():
    assert (
        _authorize((), _empty_inventory()).authorization_contract_sha256
        != _authorize(
            (), replace(_empty_inventory(), is_default=True)
        ).authorization_contract_sha256
    )


@pytest.mark.parametrize("default", [False, True])
def test_inventory_parser_preserves_empty_workspace_witness(default):
    proof = runtime._parse_postgres_workspace_inventory(
        [(dev.WORKSPACE, dev.ORG, default, None, None, None)],
        organization_id=dev.ORG,
        workspace_id=dev.WORKSPACE,
    )
    assert proof.bindings == () and proof.is_default is default
    assert _authorize((), proof).project_ids == ()


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [(dev.WORKSPACE, dev.ORG, False, None, dev.ORG, None)],
        [(dev.OTHER_WORKSPACE, dev.ORG, False, None, None, None)],
        [(dev.WORKSPACE, dev.OTHER_ORG, False, None, None, None)],
        [(dev.WORKSPACE, dev.ORG, 0, None, None, None)],
        [(dev.WORKSPACE, dev.ORG, False, dev.PROJECT, dev.ORG, None)],
        [(dev.WORKSPACE, dev.ORG, True, dev.PROJECT, dev.OTHER_ORG, None)],
        [(dev.WORKSPACE, dev.ORG, True, dev.PROJECT, dev.ORG, dev.OTHER_WORKSPACE)],
        [(dev.WORKSPACE, dev.ORG, False, None, None, None)] * 2,
    ],
)
def test_inventory_parser_rejects_absent_malformed_or_foreign_workspace(rows):
    with pytest.raises(runtime.PropertyCatalogDevRuntimeError):
        runtime._parse_postgres_workspace_inventory(
            rows, organization_id=dev.ORG, workspace_id=dev.WORKSPACE
        )


def test_default_workspace_legacy_project_mapping_is_snapshot_owned():
    proof = runtime._parse_postgres_workspace_inventory(
        [(dev.WORKSPACE, dev.ORG, True, dev.PROJECT, dev.ORG, None)],
        organization_id=dev.ORG,
        workspace_id=dev.WORKSPACE,
    )
    assert proof.bindings == dev._project_bindings((dev.PROJECT,)).bindings
    assert _authorize((dev.PROJECT,), proof).workspace_is_default


@pytest.mark.parametrize("current_snapshot", [False, True])
@pytest.mark.parametrize("changed_identity", [False, True])
def test_inventory_probe_rechecks_pinned_pg_and_exact_snapshot(
    monkeypatch, current_snapshot, changed_identity
):
    import django.db

    identity = dev._provenance_observation().postgres
    identity_row = (
        identity.database,
        identity.user,
        identity.session_user,
        identity.server_address,
        identity.server_port,
        True,
        False,
        False,
        False,
        False,
        False,
        "on",
        "on",
        0,
    )
    if changed_identity:
        identity_row = ("other_database", *identity_row[1:])
    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, sql, params=None):
            statements.append((sql, params))

        def fetchone(self):
            row = getattr(self, "returned", False)
            self.returned = True
            return None if row else identity_row

        def fetchall(self):
            return [(dev.WORKSPACE, dev.ORG, False, None, None, None)]

    class Atomic:
        def __enter__(self):
            connection.in_atomic_block = True

        def __exit__(self, *_):
            connection.in_atomic_block = False

    connection = SimpleNamespace(
        vendor="postgresql", in_atomic_block=current_snapshot, cursor=Cursor
    )
    monkeypatch.setattr(django.db, "connections", {"default": connection})
    monkeypatch.setattr(django.db.transaction, "atomic", lambda **_: Atomic())
    kwargs = {
        "organization_id": dev.ORG,
        "workspace_id": dev.WORKSPACE,
        "current_snapshot": current_snapshot,
    }
    if changed_identity:
        with pytest.raises(
            runtime.PropertyCatalogDevRuntimeError, match="PostgreSQL identity"
        ):
            runtime._postgres_workspace_project_inventory((), identity, **kwargs)
        assert all(
            sql != runtime._POSTGRES_WORKSPACE_INVENTORY_SQL for sql, _ in statements
        )
        return
    assert (
        runtime._postgres_workspace_project_inventory((), identity, **kwargs)
        == _empty_inventory()
    )
    assert statements[-1] == (
        runtime._POSTGRES_WORKSPACE_INVENTORY_SQL,
        (dev.WORKSPACE, dev.ORG),
    )
    sql = statements[-1][0]
    for requirement in (
        "INNER JOIN public.accounts_organization",
        "workspace.is_active = TRUE",
        "workspace.deleted = FALSE",
        "project.deleted = FALSE",
        "project.trace_type = 'observe'",
        "workspace.is_default AND project.workspace_id IS NULL",
        "LIMIT 257",
    ):
        assert requirement in sql
    assert any("REPEATABLE READ, READ ONLY" in sql for sql, _ in statements) is (
        not current_snapshot
    )


def test_factory_empty_scope_rechecks_inventory_before_reader_publication(
    tmp_path, monkeypatch
):
    settings = dev._runtime_settings(str(tmp_path))
    settings.PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST = []
    settings._PROPERTY_CATALOG_MANAGED_INSTALLATION = True
    observed = [()]
    calls = []

    def probe(projects, identity, **scope):
        assert projects == () and identity == dev._provenance_observation().postgres
        assert scope == {"organization_id": dev.ORG, "workspace_id": dev.WORKSPACE}
        calls.append(scope)
        return dev._project_bindings(observed[0])

    monkeypatch.setattr(runtime, "_postgres_workspace_project_inventory", probe)
    factory = runtime.PropertyCatalogDevRuntimeFactory(
        settings_object=settings,
        native_client_factory=Driver,
        provenance_probe=lambda *_: dev._provenance_observation(),
        now=lambda: dev.ATTESTED_AT,
    )
    # The service's authorize callback is tested separately; this invokes the
    # exact factory-owned gate it uses before control publication.
    # Write admission/transport is separately covered; this fixture is read-only.
    factory._managed_native_writer = lambda *args, **kwargs: None
    subject = factory(dev._request(execute=True))
    try:
        assert subject._refresh_project_tenant_authorization().project_ids == ()
        observed[0] = (dev.PROJECT,)
        with pytest.raises(
            runtime.PropertyCatalogDevRuntimeError, match="missing exact"
        ):
            subject._refresh_project_tenant_authorization()
        assert len(calls) == 3
    finally:
        subject.close()


@pytest.mark.parametrize("bad", [None, "", ","])
def test_missing_project_setting_does_not_mean_proven_empty(bad):
    with pytest.raises(runtime.PropertyCatalogDevRuntimeError):
        runtime._project_allowlist_setting(
            SimpleNamespace(PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST=bad)
        )
    with pytest.raises(runtime.PropertyCatalogDevRuntimeError):
        runtime._project_allowlist_setting(SimpleNamespace())
    assert (
        runtime._project_allowlist_setting(
            SimpleNamespace(PROPERTY_CATALOG_DEV_PROJECT_ALLOWLIST=[])
        )
        == ()
    )


@pytest.mark.parametrize(
    "make",
    [
        lifecycle._scope,
        spans._catalog_group_context,
        lambda: spans._authoritative_case()[0],
        dev._unit_runtime_config,
    ],
)
def test_scope_types_require_explicit_unique_bounded_inventory(make):
    original = make()
    assert replace(original, project_ids=()).project_ids == ()
    for bad in (
        None,
        [],
        (dev.PROJECT, dev.PROJECT),
        tuple(str(__import__("uuid").UUID(int=i + 1)) for i in range(257)),
    ):
        with pytest.raises(
            (TypeError, ValueError, runtime.PropertyCatalogDevRuntimeError)
        ):
            replace(original, project_ids=bad)


def test_empty_build_plan_and_fence_roundtrip_without_new_wire_version():
    clock = lifecycle._Clock(lifecycle.INITIAL_UNTIL)
    subject = lifecycle._lifecycle(
        state=lifecycle._State(),
        clock=clock,
        freezer=lifecycle._Freezer(clock),
        tokens=[lifecycle.TOKEN_A],
    )
    prepared = subject.prepare(
        scope=replace(lifecycle._scope(), project_ids=()),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=lifecycle._bounds(),
    )
    plan = prepared.lease.build_plan
    assert len(plan.streams) == 10 and plan.source_scope.project_ids == ()
    assert RevisionBuildPlan.from_json(plan.canonical_json) == plan
    raw = encode_revision_fence_registry(
        (fences._assignment(project_ids=()),), now=fences.NOW
    )
    assert decode_revision_fence_registry(raw, now=fences.NOW)[0]["project_ids"] == []
    for missing in (False, True):
        document = json.loads(raw)
        scope = document["fences"][0]
        if missing:
            del scope["project_ids"]
        else:
            scope["project_ids"] = None
        with pytest.raises(RevisionFenceRegistryError):
            decode_revision_fence_registry(
                json.dumps(document).encode(), now=fences.NOW
            )
        document = json.loads(plan.canonical_json)
        scope = document["source_scope"]
        if missing:
            del scope["project_ids"]
        else:
            scope["project_ids"] = None
        with pytest.raises(ValueError):
            RevisionBuildPlan.from_json(canonical_json(document))
    complete = lifecycle._completion(prepared, absence_pass=True)
    complete.validate_for(prepared)
    for omitted in ("opened_streams", "stream_drain_proofs", "checkpoints"):
        with pytest.raises(DurableLifecycleError, match="ten"):
            replace(
                complete, **{omitted: getattr(complete, omitted)[:-1]}
            ).validate_for(prepared)


class _ClockOnlySpanClient:
    source_database = spans.SOURCE_DATABASE

    def __init__(self):
        self.calls = []

    def query(self, sql, params, **kwargs):
        assert sql == _EMPTY_FENCE_SQL, (
            "empty project set attempted a source-table read"
        )
        assert kwargs["settings"]["readonly"] == 2
        self.calls.append(sql)
        return ({"audit_generation": 77},)

    def source_parts(self, **_):
        raise AssertionError("empty scope attempted parts metadata")


def test_empty_span_sources_emit_real_terminal_audit_and_checkpoint_proofs():
    frozen, build, _, publishers, store = spans._authoritative_case()
    client = _ClockOnlySpanClient()
    reader = spans._reader(client)
    frozen = reader.freeze(project_ids=[], since=frozen.since, until=frozen.until)
    assert frozen.units == () and frozen.audit_generation == 77
    assert (
        reader.read_page(frozen).terminal
        and reader.read_page(frozen, cursor="").spans == ()
    )
    with pytest.raises(PropertyCatalogSpanSourceError, match="resume"):
        reader.read_page(frozen, cursor=SpanScanCursor(0).encode())
    assert reader.audit(frozen) == SpanAuditAccumulator().proof
    assert (
        reader.newly_versioned_history(
            project_ids=[], since=frozen.since, until=frozen.until
        )
        is None
    )
    assert (
        reader.history_in_parts(
            project_ids=[], since=frozen.since, until=frozen.until, part_names=()
        )
        is None
    )
    store.fail_first_append = False
    reconciler = AuthoritativeSpanReconciler(
        reader=reader, publishers=publishers, checkpoint_store=store
    )
    result = reconciler.run(frozen=frozen, build=build)
    assert (
        result.values.source_count
        == result.values.value_count
        == result.source_audit.source_count
        == 0
    )
    assert result.values.delivery_count == 1 and result.source_audit.delivery_count == 2
    for checkpoint in (result.values, result.source_audit):
        assert (
            checkpoint.terminal and checkpoint.status == spans.CheckpointStatus.COMPLETE
        )
        assert checkpoint.source_digest == SpanAuditAccumulator().proof.digest
        assert checkpoint.terminal_payload_sha256 != "0" * 64
        assert store.latest[checkpoint.producer_stream_id].watermark == "77"
    assert [
        call[0].terminal for call in publishers[AuthoritativeSpanRole.VALUES].calls
    ] == [True]
    assert [
        call[0].terminal
        for call in publishers[AuthoritativeSpanRole.SOURCE_AUDIT].calls
    ] == [False, True]
    counts = {role: len(publisher.calls) for role, publisher in publishers.items()}
    assert reconciler.run(frozen=frozen, build=build) == result
    assert counts == {
        role: len(publisher.calls) for role, publisher in publishers.items()
    }
    assert client.calls == [_EMPTY_FENCE_SQL]


def test_empty_span_group_loaders_do_not_query_sources_or_old_groups():
    frozen = replace(spans._authoritative_case()[0], project_ids=())
    context = replace(spans._catalog_group_context(), project_ids=())
    reader = spans._reader(_ClockOnlySpanClient())
    direct = CanonicalSpanAttributeGroupPageLoader(reader, frozen)
    assert direct(context=context, cursor=None, limit=5) == ()
    client = spans._CatalogGroupClient(())
    pinned = RevisionPinnedSpanAttributeGroupPageLoader(
        client,
        context=context,
        build_token=spans.BUILD_TOKEN,
        deadline=spans._FixedDeadline(7000),
    )
    assert pinned(context=context, cursor=None, limit=5) == ()
    assert client.calls == []


def test_empty_runtime_does_not_watch_parts_or_probe_history():
    subject = object.__new__(runtime.CheckedInPropertyCatalogDevRuntime)
    execution = SimpleNamespace(
        prepared=SimpleNamespace(scope=SimpleNamespace(project_ids=()))
    )
    subject._notice_source_part_changes(execution)
    subject._notice_historical_source_change(execution)


def test_projectless_reader_keeps_workspace_definition_predicates(settings):
    settings.SECRET_KEY = "projectless-test-only"
    executor = definitions.FakeExecutor(
        [
            [definitions._activation_row(covered_project_ids=())],
            [definitions._conflict_row()],
            [],
        ]
    )
    page = definitions.PropertyCatalogReader(
        executor, catalog_database="property_catalog_dev_test"
    ).read_page(
        scope=definitions._scope(project_ids=(), workspace_scope=True),
        query=definitions.QUERY,
        page_size=50,
    )
    assert page.metrics == ()
    params = executor.calls[1]["params"]
    assert (
        params["catalog_project_ids"] == ()
        and params["catalog_include_all_projects"] == 0
    )
    assert params["catalog_include_workspace_default"] == 1


def test_projectless_value_reader_is_qualified_empty_without_value_queries(settings):
    settings.SECRET_KEY = "projectless-test-only"
    executor = values.FakeExecutor([[values._activation_row(covered_project_ids=())]])
    page = values._read(
        values._reader(executor),
        scope=values._scope(project_ids=(), workspace_scope=True),
    )
    assert not page.values and not page.has_more and page.next_cursor is None
    assert len(executor.calls) == 1


@pytest.mark.parametrize(
    "reader", [definitions.PropertyCatalogReader, values.PropertyCatalogValueReader]
)
def test_reader_missing_or_null_inventory_is_not_explicit_empty(reader):
    scope = definitions._scope(project_ids=(), workspace_scope=True)
    for missing in (False, True):
        bad = dict(scope)
        if missing:
            del bad["project_ids"]
        else:
            bad["project_ids"] = None
        with pytest.raises(ValueError, match="explicit project inventory"):
            reader._validate_scope(bad)


@pytest.mark.parametrize(
    "initial_projects,next_projects",
    [((lifecycle.PROJECT_A,), ()), ((), (lifecycle.PROJECT_A,))],
)
def test_auto_inventory_transition_requires_full_repair(
    initial_projects, next_projects
):
    clock = lifecycle._Clock(lifecycle.INITIAL_UNTIL)
    state = lifecycle._State()
    subject = lifecycle._lifecycle(
        state=state,
        clock=clock,
        freezer=lifecycle._Freezer(clock),
        tokens=[lifecycle.TOKEN_A, lifecycle.TOKEN_B],
    )
    initial = subject.prepare(
        scope=replace(lifecycle._scope(), project_ids=initial_projects),
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=lifecycle._bounds(),
    )
    state.activate(initial, at=clock.current)
    clock.current += lifecycle.timedelta(minutes=2)
    repair = subject.prepare(
        scope=replace(lifecycle._scope(), project_ids=next_projects),
        mode=LifecycleRunMode.AUTO,
        configured_bounds=lifecycle._bounds(),
    )
    assert (
        repair.mode is LifecycleRunMode.FULL_REPAIR
        and repair.scope.project_ids == next_projects
    )
    assert len(repair.streams) == 10 and all(
        stream.lower_watermark == "" for stream in repair.streams
    )


class _InventoryRecoveryCoordinator(lifecycle._Coordinator):
    """Exercise lifecycle dispatch; durable CAS/journal has its own tests."""

    def __init__(self, state):
        super().__init__(state)
        self.invalidated = []
        self.retired = []

    def invalidate_source_snapshot(self, lease):
        assert self.state.reservation.lease == lease
        assert self.state.reservation.status in (
            ReservationStatus.OPEN,
            ReservationStatus.DRAINING,
        )
        self.invalidated.append(lease)

    def source_snapshot_invalid(self, lease):
        return lease in self.invalidated

    def replace_expired(self, *, expired_reservation, prior_active, **allocation):
        assert (
            self.state.reservation == expired_reservation
            and self.state.active == prior_active
        )
        assert self.source_snapshot_invalid(expired_reservation.lease)
        old = expired_reservation.lease
        self.retired.append(old)
        plan = replace(
            old.build_plan,
            catalog_revision=old.catalog_revision + 1,
            build_token=allocation["build_token"],
            source_scope=allocation["source_scope"],
            streams=allocation["planned_streams"],
        )
        fresh = replace(
            old,
            catalog_revision=plan.catalog_revision,
            build_token=plan.build_token,
            build_plan_json=plan.canonical_json,
            build_lease_sha256=plan.sha256,
            issued_at=allocation["now"],
            expires_at=allocation["now"] + lifecycle.timedelta(minutes=10),
        )
        self.state.reservation = lifecycle.PersistedReservation(
            fresh, ReservationStatus.OPEN
        )
        self.state.resumes = ()
        return fresh


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize(
    "status",
    [ReservationStatus.OPEN, ReservationStatus.DRAINING, ReservationStatus.FENCED],
)
def test_last_project_removal_supersedes_only_never_fenced_snapshot(active, status):
    clock = lifecycle._Clock(lifecycle.INITIAL_UNTIL)
    state = lifecycle._State()
    coordinator = _InventoryRecoveryCoordinator(state)
    subject = lifecycle._lifecycle(
        state=state,
        coordinator=coordinator,
        clock=clock,
        freezer=lifecycle._Freezer(clock),
        tokens=[lifecycle.TOKEN_A, lifecycle.TOKEN_B, lifecycle.TOKEN_C],
    )
    scope = replace(lifecycle._scope(), project_ids=(lifecycle.PROJECT_A,))
    original = subject.prepare(
        scope=scope,
        mode=LifecycleRunMode.INITIAL_BACKFILL,
        configured_bounds=lifecycle._bounds(),
    )
    if active:
        state.activate(original, at=clock.current)
        clock.current += lifecycle.timedelta(minutes=2)
        original = subject.prepare(
            scope=scope,
            mode=LifecycleRunMode.AUTO,
            configured_bounds=lifecycle._bounds(),
        )
    state.reservation = replace(state.reservation, status=status)
    request = {
        "scope": replace(scope, project_ids=()),
        "mode": LifecycleRunMode.AUTO if active else LifecycleRunMode.INITIAL_BACKFILL,
        "configured_bounds": lifecycle._bounds(),
    }
    original_json = original.lease.build_plan_json
    if status is ReservationStatus.FENCED:
        with pytest.raises(DurableLifecycleError, match="project inventory"):
            subject.prepare(**request)
        assert coordinator.invalidated == coordinator.retired == []
        return
    repaired = subject.prepare(**request)
    assert coordinator.invalidated == coordinator.retired == [original.lease]
    assert original.lease.build_plan_json == original_json
    assert repaired.lease.catalog_revision == original.lease.catalog_revision + 1
    assert repaired.scope.project_ids == () and not repaired.resumed
    assert repaired.mode is (
        LifecycleRunMode.FULL_REPAIR if active else LifecycleRunMode.INITIAL_BACKFILL
    )


def test_projectless_full_repair_retires_project_binding_but_keeps_workspace_definition():
    from tracer.services.clickhouse.v2.property_catalog.models import (
        SourceAdapter,
        VisibilityBinding,
        VisibilityScope,
    )
    from tracer.services.clickhouse.v2.property_catalog.projection import (
        project_definition,
    )
    from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
        EvalTemplateSourceAdapter,
        _make_source_record,
    )

    request = reconcile._dataset_request()
    request = replace(
        request,
        context=replace(request.context, project_ids=()),
        mode=reconcile.ReconcileMode.FULL_REPAIR,
    )
    definition = reconcile._definition()
    common = {
        "organization_id": reconcile.ORG,
        "workspace_id": reconcile.WORKSPACE,
        "catalog_epoch": 1,
        "catalog_revision": 1,
        "build_token": reconcile.BUILD,
        "projection_version": 1,
        "definition": definition,
        "source_adapter": SourceAdapter.EVAL_TEMPLATE,
        "source_entity_id": definition.source_key,
        "source_version": 1,
        "source_fingerprint": "a" * 64,
        "producer_stream_id": reconcile.STREAM,
        "producer_sequence": 1,
        "emitted_at": reconcile.NOW,
    }
    workspace_visibility = VisibilityBinding(
        VisibilityScope.WORKSPACE_DEFAULT, reconcile.WORKSPACE
    )
    old_project = project_definition(
        **common,
        visibility=VisibilityBinding(VisibilityScope.PROJECT, reconcile.PROJECT),
    )
    old_workspace = project_definition(**common, visibility=workspace_visibility)
    record = _make_source_record(
        source_adapter=SourceAdapter.EVAL_TEMPLATE,
        source_entity_id=definition.source_key,
        source_updated_at=reconcile.NOW,
        definition=definition,
        visibilities=(workspace_visibility,),
    )

    class Current:
        def read_current(self, **kwargs):
            return (old_project, old_workspace) if kwargs["at_revision"] == 1 else ()

    publisher, checkpoints = (
        reconcile._ExactWirePublisher(),
        reconcile._CheckpointSink(),
    )
    result = reconcile.PropertyCatalogReconciler(
        publisher=publisher, checkpoint_writer=checkpoints, current_bindings=Current()
    ).reconcile(
        EvalTemplateSourceAdapter(page_loader=lambda **_: (record,)),
        request,
    )
    assert result.complete and result.checkpoint_write.checkpoint.tombstone_count == 1
    rows = [row for envelope in publisher.envelopes for row in envelope.definitions]
    assert {(row.visibility_scope, row.is_deleted) for row in rows} == {
        (VisibilityScope.PROJECT, True),
        (VisibilityScope.WORKSPACE_DEFAULT, False),
    }
    assert publisher.envelopes[-1].terminal


def test_empty_source_rejects_injected_live_span_before_publication():
    frozen, build, reader, publishers, store = spans._authoritative_case()
    frozen = replace(frozen, project_ids=())
    reader.frozen = frozen
    with pytest.raises(PropertyCatalogSpanSourceError, match="frozen project scope"):
        AuthoritativeSpanReconciler(
            reader=reader, publishers=publishers, checkpoint_store=store
        ).run(frozen=frozen, build=build)
    assert not store.writes and all(
        not publisher.calls for publisher in publishers.values()
    )


def test_native_empty_clock_does_not_open_general_table_free_sql():
    class ClockDriver:
        database = spans.SOURCE_DATABASE
        server_enforced_readonly = True

        def execute_read(self, sql, *_args, **kwargs):
            assert sql == _EMPTY_FENCE_SQL and kwargs["settings"]["readonly"] == 2
            return [(77,)], [("audit_generation", "UInt64")], None

    client = runtime.NativeSourceClient(
        ClockDriver(),
        source_database=spans.SOURCE_DATABASE,
        catalog_database=spans.CATALOG_DATABASE,
    )
    assert client.query(_EMPTY_FENCE_SQL, {}, timeout_ms=1000, settings={}) == (
        {"audit_generation": 77},
    )
    for sql in (
        "SELECT 1",
        _EMPTY_FENCE_SQL + " FROM system.tables",
        _EMPTY_FENCE_SQL + "; SELECT 1",
    ):
        with pytest.raises((RuntimeError, ValueError)):
            client.query(sql, {}, timeout_ms=1000, settings={})


@pytest.mark.parametrize("bad", [None, "", {}])
def test_empty_span_scope_cannot_be_inferred_from_missing_inventory(bad):
    frozen = spans._authoritative_case()[0]
    client = _ClockOnlySpanClient()
    reader = spans._reader(client)
    for method in (reader.freeze, reader.newly_versioned_history):
        with pytest.raises(TypeError, match="explicit"):
            method(project_ids=bad, since=frozen.since, until=frozen.until)
    assert client.calls == []


@pytest.mark.parametrize(
    "bad", [{"qualified_at": None}, {"source_manifest_sha256": "invalid-sha256"}]
)
def test_empty_readers_still_reject_unqualified_or_malformed_activation(settings, bad):
    settings.SECRET_KEY = "projectless-test-only"
    executor = definitions.FakeExecutor(
        [[definitions._activation_row(covered_project_ids=(), **bad)]]
    )
    with pytest.raises(definitions.PropertyCatalogUnavailable):
        definitions.PropertyCatalogReader(
            executor, catalog_database="property_catalog_dev_test"
        ).read_page(
            scope=definitions._scope(project_ids=(), workspace_scope=True),
            query=definitions.QUERY,
            page_size=50,
        )
    assert len(executor.calls) == 1
    executor = values.FakeExecutor(
        [[values._activation_row(covered_project_ids=(), **bad)]]
    )
    with pytest.raises(values.PropertyCatalogValueUnavailable):
        values._read(
            values._reader(executor),
            scope=values._scope(project_ids=(), workspace_scope=True),
        )
    assert len(executor.calls) == 1


def test_empty_qualified_catalog_does_not_authorize_requested_external_project(
    settings,
):
    settings.SECRET_KEY = "projectless-test-only"
    executor = values.FakeExecutor([[values._activation_row(covered_project_ids=())]])
    with pytest.raises(values.PropertyCatalogValueUnavailable) as error:
        values._read(values._reader(executor), scope=values._scope())
    assert error.value.reason == "activation_scope_incomplete"
    assert len(executor.calls) == 1
