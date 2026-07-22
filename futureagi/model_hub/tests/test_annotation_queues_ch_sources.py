"""Coverage for the ClickHouse-native source paths of the annotation-queue API.

The queue endpoints resolve ``trace`` / ``observation_span`` / ``trace_session``
items from ClickHouse (the fi-collector writes those sources ONLY to CH — there
is no Postgres row). The PG-backed suites therefore never execute the CH
content-building branches in ``views/annotation_queues.py``.

Rather than stand up a real ClickHouse instance, we do what
``test_ch25_annotation_collector_source_resolution.py`` does: build a synthetic
``CHSpan`` in memory and patch ``tracer.services.clickhouse.v2.get_reader`` so the
resolver reads our fake span. That drives the real product mapping code
(field renames, JSON parsing, attrs merge) on the actual endpoints.

Covered here (endpoint × source):
  - export × observation_span (content + deleted sentinel)
  - export × trace (root-span → trace content)
  - export-to-dataset × observation_span (CH content → dataset cells)

Remaining cells to extend (same pattern, documented for the next pass):
  - trace_session: patch ``resolve_session_fields`` at
    ``tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_fields``
    (used by ``_batch_ch_session_fields``) to return session field dicts.
  - eval-metrics block: seed a PG ``EvalLogger`` row keyed to the span/trace id;
    ``_eval_metrics_for_queue_items`` reads it from Postgres, not CH.
  - for-source default-queue CH matching: patch ``session_exists`` /
    ``resolve_ch_span_source`` for the project-scope match branch.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

import pytest
from rest_framework import status

from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueLabel,
    QueueItem,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    QueueItemSourceType,
    QueueItemStatus,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from tracer.models.observation_span import EvalLogger
from tracer.models.project import Project
from tracer.services.clickhouse.v2.span_reader import CHSpan

QUEUE_URL = "/model-hub/annotation-queues/"
CH_READER_PATH = "tracer.services.clickhouse.v2.get_reader"
SESSION_FIELDS_PATH = (
    "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_fields"
)


def _result(resp):
    return resp.data.get("result", resp.data) if hasattr(resp, "data") else resp.data


def _for_source_url():
    return f"{QUEUE_URL}for-source/"


def _make_chspan(*, project_id, span_id=None, trace_id=None, parent_span_id=""):
    """A fully-populated CHSpan standing in for a collector span row (the shape
    ``CHSpanReader`` returns). No PG ObservationSpan row exists for it."""
    return CHSpan(
        id=span_id or f"ch-span-{uuid.uuid4().hex[:12]}",
        project_id=str(project_id),
        trace_id=trace_id or str(uuid.uuid4()),
        parent_span_id=parent_span_id,
        name="collector root span",
        observation_type="agent",
        operation_name="invoke_agent",
        start_time=datetime(2025, 5, 1, 10, 0, 0, tzinfo=UTC),
        end_time=datetime(2025, 5, 1, 10, 0, 2, tzinfo=UTC),
        latency_ms=2000,
        model="gpt-4o",
        provider="openai",
        prompt_tokens=11,
        completion_tokens=7,
        total_tokens=18,
        cost=0.0021,
        status="OK",
        status_message="",
        org_id=None,
        project_version_id=None,
        end_user_id=None,
        trace_session_id=None,
        prompt_version_id=None,
        prompt_label_id=None,
        custom_eval_config_id=None,
        input='{"messages": [{"role": "user", "content": "hi"}]}',
        output='{"role": "assistant", "content": "hello"}',
        tags='["collector"]',
        span_events='[{"name": "event-a"}]',
        metadata='{"k": "v"}',
        resource_attrs='{"service.name": "collector-svc"}',
        attributes_extra='{"extra.key": "extra-val"}',
        attrs_string={"gen_ai.request.model": "gpt-4o"},
        attrs_number={"gen_ai.usage.total_tokens": 18.0},
        attrs_bool={"gen_ai.stream": 1},
    )


class _ReaderCM:
    """Context-manager stub mimicking ``get_reader()`` → ``CHSpanReader``.

    Implements the methods the annotation-queue render/export paths call:
    ``list_by_ids`` (batch cache build) plus the point/trace helpers so the
    same stub can back trace/session variants later.
    """

    def __init__(self, span):
        self._span = span

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_by_ids(self, span_ids, *, project_id=None, include_heavy=True):
        ids = {str(s) for s in span_ids}
        if self._span is None:
            return []
        return [self._span] if str(self._span.id) in ids else []

    def get(self, span_id):
        if self._span is None:
            return None
        return self._span if str(span_id) == str(self._span.id) else None

    def roots_by_trace_ids(
        self, trace_ids, *, include_heavy=False, project_id=None, org_id=None
    ):
        if self._span is None or self._span.parent_span_id:
            return []
        return (
            [self._span]
            if str(self._span.trace_id) in {str(t) for t in trace_ids}
            else []
        )

    def root_ids_by_trace_ids(self, trace_ids, project_ids=None):
        """``{trace_id: (root_span_id, project_id)}`` — used by for-source matching."""
        if self._span is None or self._span.parent_span_id:
            return {}
        ids = {str(t) for t in trace_ids}
        if str(self._span.trace_id) not in ids:
            return {}
        return {
            str(self._span.trace_id): (str(self._span.id), str(self._span.project_id))
        }

    def scope_by_ids(self, span_ids):
        """``{span_id: scope}`` where ``scope.project_id`` — for-source span match."""
        ids = {str(s) for s in span_ids}
        if self._span is None or str(self._span.id) not in ids:
            return {}
        return {str(self._span.id): SimpleNamespace(project_id=str(self._span.project_id))}


def _project(organization, workspace):
    return Project.objects.create(
        name=f"ch-proj-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )


def _queue(organization, workspace, user, project):
    return AnnotationQueue.objects.create(
        name=f"ch-q-{uuid.uuid4().hex[:8]}",
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        organization=organization,
        workspace=workspace,
        project=project,
        created_by=user,
    )


def _span_item(organization, workspace, queue, span, project, status=None):
    return QueueItem.objects.create(
        queue=queue,
        source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
        observation_span_id=span.id,
        project=project,
        organization=organization,
        workspace=workspace,
        status=status or QueueItemStatus.PENDING.value,
    )


def _trace_item(organization, workspace, queue, span, project):
    return QueueItem.objects.create(
        queue=queue,
        source_type=QueueItemSourceType.TRACE.value,
        trace_id=span.trace_id,
        project=project,
        organization=organization,
        workspace=workspace,
        status=QueueItemStatus.PENDING.value,
    )


@pytest.mark.django_db
class TestExportCollectorSpanContent:
    def test_export_renders_observation_span_content_from_ch(
        self, auth_client, organization, workspace, user
    ):
        project = _project(organization, workspace)
        span = _make_chspan(project_id=project.id)
        queue = _queue(organization, workspace, user, project)
        item = _span_item(organization, workspace, queue, span, project)

        with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
            resp = auth_client.get(
                f"{QUEUE_URL}{queue.id}/export/", {"export_format": "json"}
            )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        rows = _result(resp)
        entry = next(r for r in rows if r["item_id"] == str(item.id))
        source = entry["source"]

        # The CH content-building branch ran (not the "deleted" sentinel).
        assert source["type"] == QueueItemSourceType.OBSERVATION_SPAN.value
        assert "deleted" not in source
        assert source["span_id"] == str(span.id)
        assert source["status"] == "OK"
        # attrs_* + attributes_extra merged into span_attributes.
        assert source["span_attributes"]["gen_ai.request.model"] == "gpt-4o"
        assert source["span_attributes"]["extra.key"] == "extra-val"
        # JSON-string CH columns parsed into Python containers.
        assert source["metadata"] == {"k": "v"}

    def test_export_span_missing_from_ch_renders_deleted_sentinel(
        self, auth_client, organization, workspace, user
    ):
        """When the span is gone from CH too, the export still succeeds and
        renders the deleted sentinel — the fail-open branch."""
        project = _project(organization, workspace)
        span = _make_chspan(project_id=project.id)
        queue = _queue(organization, workspace, user, project)
        item = _span_item(organization, workspace, queue, span, project)

        # Reader returns nothing → cache miss → deleted sentinel.
        with mock.patch(CH_READER_PATH, return_value=_ReaderCM(None)):
            resp = auth_client.get(
                f"{QUEUE_URL}{queue.id}/export/", {"export_format": "json"}
            )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        rows = _result(resp)
        entry = next(r for r in rows if r["item_id"] == str(item.id))
        assert entry["source"].get("deleted") is True


@pytest.mark.django_db
class TestExportCollectorTraceContent:
    def test_export_renders_trace_content_from_ch(
        self, auth_client, organization, workspace, user
    ):
        project = _project(organization, workspace)
        # A root span (parentless) is what a trace resolves its content from.
        span = _make_chspan(project_id=project.id, parent_span_id="")
        queue = _queue(organization, workspace, user, project)
        item = _trace_item(organization, workspace, queue, span, project)

        with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
            resp = auth_client.get(
                f"{QUEUE_URL}{queue.id}/export/", {"export_format": "json"}
            )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        rows = _result(resp)
        source = next(r for r in rows if r["item_id"] == str(item.id))["source"]

        # Trace content is rendered from the CH root span, reshaped to a trace dict.
        assert source["type"] == QueueItemSourceType.TRACE.value
        assert "deleted" not in source
        assert source["trace_id"] == str(span.trace_id)
        assert "span_id" not in source  # dropped for trace items
        assert source["metadata"] == {"k": "v"}


@pytest.mark.django_db
class TestExportToDatasetCollectorSpan:
    def test_span_content_written_to_dataset(
        self, auth_client, organization, workspace, user
    ):
        project = _project(organization, workspace)
        span = _make_chspan(project_id=project.id)
        queue = _queue(organization, workspace, user, project)
        # export-to-dataset defaults to status_filter="completed".
        _span_item(
            organization,
            workspace,
            queue,
            span,
            project,
            status=QueueItemStatus.COMPLETED.value,
        )

        with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
            resp = auth_client.post(
                f"{QUEUE_URL}{queue.id}/export-to-dataset/",
                {"dataset_name": "CH Span Export DS"},
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        result = _result(resp)
        assert result["rows_created"] == 1
        assert result["columns"]  # source-derived columns were created


@pytest.mark.django_db
class TestExportCollectorSessionContent:
    def test_export_renders_trace_session_content_from_ch(
        self, auth_client, organization, workspace, user
    ):
        project = _project(organization, workspace)
        session_id = str(uuid.uuid4())
        queue = _queue(organization, workspace, user, project)
        item = QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.TRACE_SESSION.value,
            trace_session_id=session_id,
            project=project,
            organization=organization,
            workspace=workspace,
            status=QueueItemStatus.PENDING.value,
        )
        # Sessions resolve via resolve_session_fields (not the span reader).
        session_fields = {
            session_id: {
                "display_name": "My Session",
                "external_session_id": "ext-1",
                "project_id": str(project.id),
            }
        }

        with mock.patch(SESSION_FIELDS_PATH, return_value=session_fields):
            resp = auth_client.get(
                f"{QUEUE_URL}{queue.id}/export/", {"export_format": "json"}
            )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        source = next(
            r for r in _result(resp) if r["item_id"] == str(item.id)
        )["source"]
        assert source["type"] == QueueItemSourceType.TRACE_SESSION.value
        assert "deleted" not in source
        assert source["session_id"] == session_id
        assert source["name"] == "My Session"


@pytest.mark.django_db
class TestExportEvalMetrics:
    def test_span_eval_logs_surface_in_export(
        self, auth_client, organization, workspace, user
    ):
        project = _project(organization, workspace)
        span = _make_chspan(project_id=project.id)
        queue = _queue(organization, workspace, user, project)
        item = _span_item(organization, workspace, queue, span, project)

        # A PG EvalLogger row keyed to the span (span target → span + trace set).
        EvalLogger.objects.create(
            observation_span_id=span.id,
            trace_id=span.trace_id,
            eval_type_id="toxicity",
            output_float=0.9,
        )

        with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
            resp = auth_client.get(
                f"{QUEUE_URL}{queue.id}/export/", {"export_format": "json"}
            )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        entry = next(r for r in _result(resp) if r["item_id"] == str(item.id))
        evals = entry["evals"]
        assert "toxicity" in evals
        # Export unwraps a single-entry list to the entry itself.
        tox = evals["toxicity"]
        tox = tox[0] if isinstance(tox, list) else tox
        assert tox["score"] == 0.9


@pytest.mark.django_db
class TestForSourceDefaultQueueCHMatch:
    def test_span_matches_project_scoped_default_queue_via_ch(
        self, auth_client, organization, workspace, user
    ):
        project = _project(organization, workspace)
        span = _make_chspan(project_id=project.id)
        # A project-scoped DEFAULT queue with NO QueueItem for this span, so the
        # for-source CH scope-match branch decides membership.
        label = AnnotationsLabels.objects.create(
            name="ch-label",
            type="categorical",
            organization=organization,
            workspace=workspace,
            settings={
                "options": [{"label": "A"}, {"label": "B"}],
                "multi_choice": False,
                "rule_prompt": "",
                "auto_annotate": False,
                "strategy": None,
            },
        )
        default_q = AnnotationQueue.objects.create(
            name=f"default-{uuid.uuid4().hex[:8]}",
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            is_default=True,
            organization=organization,
            workspace=workspace,
            project=project,
            created_by=user,
        )
        AnnotationQueueLabel.objects.create(
            queue=default_q, label=label, order=0, required=False
        )

        with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
            resp = auth_client.get(
                _for_source_url(),
                {"source_type": "observation_span", "source_id": str(span.id)},
            )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        queue_ids = {entry["queue"]["id"] for entry in _result(resp)}
        assert str(default_q.id) in queue_ids
