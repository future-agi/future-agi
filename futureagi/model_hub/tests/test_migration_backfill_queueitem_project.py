"""Tests for the 0117 QueueItem.project backfill (RunPython).

The migration resolves each NULL-project tracer source's project from ClickHouse
via the LEAN id->project readers. Tests call the migration's backfill function
directly with the live app registry and stub those readers, so no live CH runs.
"""

from __future__ import annotations

import importlib
import uuid
from unittest import mock

import pytest
from django.apps import apps as django_apps

from accounts.models.organization import Organization
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueStatusChoices,
    QueueItem,
)
from model_hub.models.choices import QueueItemSourceType, QueueItemStatus
from tracer.models.project import Project
from tracer.services.clickhouse.v2.span_reader import SpanScope

_migration = importlib.import_module(
    "model_hub.migrations.0117_backfill_queueitem_project"
)

GET_READER = "tracer.services.clickhouse.v2.get_reader"
SESSION_FIELDS = (
    "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_fields"
)


def _run_backfill():
    _migration.backfill_queueitem_project(django_apps, None)


def _make_project(*, organization, workspace, name="backfill-proj"):
    return Project.objects.create(
        name=f"{name} {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )


def _queue(*, organization, workspace, user):
    return AnnotationQueue.objects.create(
        name=f"q-{uuid.uuid4().hex[:8]}",
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        organization=organization,
        workspace=workspace,
        created_by=user,
    )


def _item(*, queue, organization, workspace, source_type, **source):
    return QueueItem.objects.create(
        queue=queue,
        source_type=source_type,
        organization=organization,
        workspace=workspace,
        status=QueueItemStatus.PENDING.value,
        **source,
    )


def _fake_reader(*, scope=None, roots=None):
    reader = mock.MagicMock()
    reader.__enter__.return_value = reader
    reader.__exit__.return_value = False
    reader.scope_by_ids.return_value = scope or {}
    reader.root_ids_by_trace_ids.return_value = roots or {}
    return reader


@pytest.mark.django_db
def test_stamps_all_tracer_kinds_from_ch(organization, workspace, user):
    """span / trace / session items resolve their project from CH and get stamped.

    Fails on revert: without the migration the items keep project=NULL.
    """
    project = _make_project(organization=organization, workspace=workspace)
    queue = _queue(organization=organization, workspace=workspace, user=user)

    span_id = f"span-{uuid.uuid4().hex[:12]}"
    trace_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    span_item = _item(
        queue=queue,
        organization=organization,
        workspace=workspace,
        source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
        observation_span_id=span_id,
    )
    trace_item = _item(
        queue=queue,
        organization=organization,
        workspace=workspace,
        source_type=QueueItemSourceType.TRACE.value,
        trace_id=trace_id,
    )
    session_item = _item(
        queue=queue,
        organization=organization,
        workspace=workspace,
        source_type=QueueItemSourceType.TRACE_SESSION.value,
        trace_session_id=session_id,
    )

    reader = _fake_reader(
        scope={span_id: SpanScope(project_id=str(project.id), trace_id=trace_id)},
        roots={trace_id: (f"root-{trace_id}", str(project.id))},
    )
    with mock.patch(GET_READER, return_value=reader), mock.patch(
        SESSION_FIELDS, return_value={session_id: {"project_id": str(project.id)}}
    ):
        _run_backfill()

    for item in (span_item, trace_item, session_item):
        item.refresh_from_db()
        assert str(item.project_id) == str(project.id)


@pytest.mark.django_db
def test_unresolvable_source_left_null(organization, workspace, user):
    queue = _queue(organization=organization, workspace=workspace, user=user)
    span_item = _item(
        queue=queue,
        organization=organization,
        workspace=workspace,
        source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
        observation_span_id=f"span-{uuid.uuid4().hex[:12]}",
    )

    with mock.patch(GET_READER, return_value=_fake_reader()), mock.patch(
        SESSION_FIELDS, return_value={}
    ):
        _run_backfill()

    span_item.refresh_from_db()
    assert span_item.project_id is None


@pytest.mark.django_db
def test_project_in_other_org_left_null(organization, workspace, user):
    """Fail-closed org guard: a resolved project in a DIFFERENT org is never
    stamped — a wrong project would make the scoped read miss the source."""
    other_org = Organization.objects.create(name="Other Org")
    other_project = _make_project(organization=other_org, workspace=workspace)

    queue = _queue(organization=organization, workspace=workspace, user=user)
    span_id = f"span-{uuid.uuid4().hex[:12]}"
    span_item = _item(
        queue=queue,
        organization=organization,
        workspace=workspace,
        source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
        observation_span_id=span_id,
    )

    reader = _fake_reader(
        scope={span_id: SpanScope(project_id=str(other_project.id), trace_id=None)}
    )
    with mock.patch(GET_READER, return_value=reader), mock.patch(
        SESSION_FIELDS, return_value={}
    ):
        _run_backfill()

    span_item.refresh_from_db()
    assert span_item.project_id is None


@pytest.mark.django_db
def test_clickhouse_outage_is_fail_open(organization, workspace, user):
    """A CH outage during migrate must not raise (it would block the deploy);
    the row simply stays NULL for a later run."""
    queue = _queue(organization=organization, workspace=workspace, user=user)
    span_item = _item(
        queue=queue,
        organization=organization,
        workspace=workspace,
        source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
        observation_span_id=f"span-{uuid.uuid4().hex[:12]}",
    )

    with mock.patch(GET_READER, side_effect=RuntimeError("clickhouse unreachable")):
        _run_backfill()  # must not raise

    span_item.refresh_from_db()
    assert span_item.project_id is None


@pytest.mark.django_db
def test_rerun_is_idempotent(organization, workspace, user):
    """After the first run stamps a row, a second run finds nothing to do."""
    project = _make_project(organization=organization, workspace=workspace)
    queue = _queue(organization=organization, workspace=workspace, user=user)
    span_id = f"span-{uuid.uuid4().hex[:12]}"
    span_item = _item(
        queue=queue,
        organization=organization,
        workspace=workspace,
        source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
        observation_span_id=span_id,
    )

    reader = _fake_reader(
        scope={span_id: SpanScope(project_id=str(project.id), trace_id=None)}
    )
    with mock.patch(GET_READER, return_value=reader) as get_reader_mock, mock.patch(
        SESSION_FIELDS, return_value={}
    ):
        _run_backfill()
        first_calls = get_reader_mock.call_count
        _run_backfill()  # row is scoped now → filter matches nothing → no CH read
        assert get_reader_mock.call_count == first_calls

    span_item.refresh_from_db()
    assert str(span_item.project_id) == str(project.id)
