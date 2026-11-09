"""Upgrade parity for 0117 without replaying an already-applied migration.

Apply only the new RunPython to the historical state at the previous leaf.
PostgreSQL is real; only the ClickHouse client is stubbed, so the real session
resolver (including input/survivor remapping and overlay opt-out) is exercised.
"""

import importlib
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

import pytest
from django.db import connections, migrations
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from accounts.models.organization import Organization
from model_hub.models.annotation_queues import AnnotationQueue, QueueItem
from tracer.models.project import Project
from tracer.models.trace_session import TraceSessionOverlay
from tracer.services.clickhouse.v2 import trace_session_dict_reader as session_reader

PREVIOUS = ("model_hub", "0139_merge_dsx3f_c7_and_pgrid")
TARGET = ("model_hub", "0140_rebackfill_queueitem_project_upgraded")
ALIAS = "default_direct"
pytestmark = pytest.mark.django_db(transaction=True, databases={"default", ALIAS})

# Intentionally fails collection at RED: editing 0117 alone cannot repair an
# installation which has already recorded that migration as applied.
_upgrade = importlib.import_module(f"model_hub.migrations.{TARGET[1]}")
_original = importlib.import_module(
    "model_hub.migrations.0117_backfill_queueitem_project"
)


@pytest.fixture(scope="module")
def migration_loader():
    return MigrationLoader(None)


@pytest.fixture
def queue_context(organization, workspace, user):
    project = Project.objects.create(
        name=f"Upgrade project {uuid.uuid4()}",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    queue = AnnotationQueue.objects.create(
        name=f"Upgrade queue {uuid.uuid4()}",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    return SimpleNamespace(project=project, queue=queue)


def _item(context, *, source_type="trace_session", **fields):
    if source_type == "trace_session":
        fields.setdefault("trace_session_id", uuid.uuid4())
    return QueueItem.objects.create(
        queue=context.queue,
        organization_id=context.project.organization_id,
        workspace_id=context.project.workspace_id,
        source_type=source_type,
        **fields,
    )


def _session_rows(monkeypatch, projects, *, remapped=False):
    """Only CH is synthetic; map returned input ids to distinct survivor ids."""
    survivors = {
        str(session_id): str(uuid.uuid4()) if remapped else str(session_id)
        for session_id in projects
    }

    def query(sql, *, parameters):
        return SimpleNamespace(
            result_rows=[
                (
                    sid,
                    survivors[sid],
                    "Synthetic session",
                    timezone.now(),
                    str(projects[sid]),
                )
                for sid in parameters["ids"]
                if sid in projects
            ]
        )

    client = SimpleNamespace(query=mock.Mock(side_effect=query))
    monkeypatch.setattr(session_reader, "_get_client", lambda: client)
    return client


@contextmanager
def _guard_reads(alias):
    """Do not let 0117's fail-open handler hide misrouted or overlay SQL."""
    forbidden = []

    def reject_default(execute, sql, params, many, context):
        forbidden.append("default SQL")
        raise AssertionError("migration must use schema_editor.connection.alias")

    def reject_overlay(*args, **kwargs):
        forbidden.append("overlay read")
        raise AssertionError("project-only backfill must not read UI overlays")

    with mock.patch.object(
        TraceSessionOverlay.objects, "filter", side_effect=reject_overlay
    ):
        try:
            if alias == "default":
                yield
            else:
                with connections["default"].execute_wrapper(reject_default):
                    yield
        finally:
            assert forbidden == [], f"forbidden reads: {forbidden}"


def _apply_upgrade(loader, alias="default"):
    state = loader.project_state([PREVIOUS])
    # No predecessor is executed: all migrations through 0139 are already done.
    with _guard_reads(alias), connections[alias].schema_editor(atomic=False) as editor:
        loader.disk_migrations[TARGET].apply(state, editor)


def test_new_data_migration_is_after_all_current_leaves(migration_loader):
    migration = migration_loader.disk_migrations[TARGET]
    assert migration.dependencies == [PREVIOUS]
    assert TARGET in migration_loader.graph.leaf_nodes("model_hub")
    before = set(migration_loader.graph.forwards_plan(PREVIOUS))
    after = set(migration_loader.graph.forwards_plan(TARGET))
    assert after - before == {TARGET}
    assert migration.atomic is False
    assert len(migration.operations) == 1
    operation = migration.operations[0]
    assert isinstance(operation, migrations.RunPython)
    assert operation.reverse_code is migrations.RunPython.noop
    assert migration_loader.project_state([PREVIOUS]).models == (
        migration_loader.project_state([TARGET]).models
    )


@pytest.mark.parametrize("alias", ["default", ALIAS])
@pytest.mark.parametrize("remapped", [False, True])
def test_upgraded_session_is_stamped_without_overlay_and_rerun_is_noop(
    migration_loader, queue_context, monkeypatch, alias, remapped
):
    item = _item(queue_context)
    assert item.project_id is None
    client = _session_rows(
        monkeypatch,
        {str(item.trace_session_id): queue_context.project.id},
        remapped=remapped,
    )
    with mock.patch.object(
        session_reader,
        "resolve_session_fields",
        wraps=session_reader.resolve_session_fields,
    ) as resolve:
        _apply_upgrade(migration_loader, alias)
        item.refresh_from_db(using=alias)
        assert item.project_id == queue_context.project.id
        resolve.assert_called_once_with(
            [str(item.trace_session_id)], include_overlay=False
        )
        assert client.query.call_count == 1

        writes = []

        def record(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith("UPDATE"):
                writes.append(sql)
            return execute(sql, params, many, context)

        with connections[alias].execute_wrapper(record):
            _apply_upgrade(migration_loader, alias)
        item.refresh_from_db(using=alias)
        assert item.project_id == queue_context.project.id
        assert writes == []
        assert resolve.call_count == 1
        assert client.query.call_count == 1


@pytest.mark.parametrize(
    "case",
    [
        "wrong_org",
        "unresolved",
        "missing_project",
        "already_stamped",
        "dataset_row",
        "call_execution",
        "trace",
        "observation_span",
    ],
)
def test_ineligible_or_unresolved_items_are_unchanged(
    migration_loader, queue_context, monkeypatch, case
):
    fields = {}
    source_type = "trace_session"
    resolved_project = queue_context.project.id
    if case == "wrong_org":
        other_org = Organization.objects.create(name=f"Other org {uuid.uuid4()}")
        resolved_project = Project.objects.create(
            name=f"Other project {uuid.uuid4()}",
            organization=other_org,
            workspace_id=queue_context.project.workspace_id,
            model_type="GenerativeLLM",
            trace_type="observe",
        ).id
    elif case == "missing_project":
        resolved_project = uuid.uuid4()
    elif case == "already_stamped":
        existing = Project.objects.create(
            name=f"Existing project {uuid.uuid4()}",
            organization_id=queue_context.project.organization_id,
            workspace_id=queue_context.project.workspace_id,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
        fields["project_id"] = existing.id
    elif case in {"dataset_row", "call_execution", "trace", "observation_span"}:
        source_type = case
        if case == "trace":
            fields["trace_id"] = uuid.uuid4()
        elif case == "observation_span":
            fields["observation_span_id"] = uuid.uuid4().hex[:16]

    item = _item(queue_context, source_type=source_type, **fields)
    before = QueueItem.objects.filter(pk=item.pk).values().get()
    projects = (
        {} if case == "unresolved" else {str(item.trace_session_id): resolved_project}
    )
    client = _session_rows(monkeypatch, projects)
    reader = mock.MagicMock()
    reader.__enter__.return_value = reader
    reader.scope_by_ids.return_value = {}
    reader.root_ids_by_trace_ids.return_value = {}
    with mock.patch(
        "tracer.services.clickhouse.v2.get_reader", return_value=reader
    ) as get_reader:
        _apply_upgrade(migration_loader, ALIAS)
        _apply_upgrade(migration_loader, ALIAS)
    assert QueueItem.objects.using(ALIAS).filter(pk=item.pk).values().get() == before
    if case in {"already_stamped", "dataset_row", "call_execution"}:
        client.query.assert_not_called()
        get_reader.assert_not_called()


@pytest.mark.parametrize("alias", ["default", ALIAS])
@pytest.mark.parametrize("source_type", ["trace", "observation_span"])
def test_resolvable_trace_and_span_items_remain_null(
    migration_loader, queue_context, monkeypatch, alias, source_type
):
    source_id = uuid.uuid4() if source_type == "trace" else uuid.uuid4().hex[:16]
    item = _item(
        queue_context, source_type=source_type, **{f"{source_type}_id": source_id}
    )
    assert item.project_id is None
    before = QueueItem.objects.filter(pk=item.pk).values().get()
    project_id = str(queue_context.project.id)
    client = _session_rows(monkeypatch, {})
    reader = mock.MagicMock()
    reader.__enter__.return_value = reader
    # Both lean CH readers can resolve this source to its own org's Project.
    # An all-tracer replay would stamp it; an unresolved fixture cannot catch that.
    reader.scope_by_ids.return_value = {
        str(source_id): SimpleNamespace(project_id=project_id)
    }
    reader.root_ids_by_trace_ids.return_value = {
        str(source_id): ("synthetic-root", project_id)
    }
    with mock.patch(
        "tracer.services.clickhouse.v2.get_reader", return_value=reader
    ) as get_reader:
        _apply_upgrade(migration_loader, alias)
        _apply_upgrade(migration_loader, alias)
    item.refresh_from_db(using=alias)
    assert item.project_id is None
    assert QueueItem.objects.using(alias).filter(pk=item.pk).values().get() == before
    client.query.assert_not_called()
    get_reader.assert_not_called()


def test_keyset_batches_continue_past_unresolved_sessions(
    migration_loader, queue_context, monkeypatch
):
    items = [_item(queue_context) for _ in range(5)]
    items.sort(key=lambda item: item.pk)
    # The first batch is entirely unresolved; later batches must still run.
    resolvable = items[2:]
    client = _session_rows(
        monkeypatch,
        {str(item.trace_session_id): queue_context.project.id for item in resolvable},
    )
    monkeypatch.setattr(_original, "_BATCH", 2)
    _apply_upgrade(migration_loader, ALIAS)
    assert client.query.call_count == 3
    assert all(
        len(call.kwargs["parameters"]["ids"]) <= 2
        for call in client.query.call_args_list
    )
    for item in items:
        item.refresh_from_db(using=ALIAS)
        expected = queue_context.project.id if item in resolvable else None
        assert item.project_id == expected
