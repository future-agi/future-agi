"""Phase 2 — ``add-items`` endpoint filter-mode tests.

Covers:
  - Backward compat: the existing ``items`` payload still works.
  - Filter-mode: happy path, exclude_ids, duplicates, signed continuation.
  - Validation: both payload forms together, neither present,
    unsupported mode, unsupported source_type.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from threading import Barrier, BrokenBarrierError, Thread
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.conf import settings as django_settings
from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone
from structlog.testing import capture_logs

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.annotation_queues import (
    FULL_ACCESS_QUEUE_ROLES,
    AnnotationQueue,
    AnnotationQueueAnnotator,
    QueueItem,
)
from model_hub.models.choices import AnnotatorRole
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.tests._ch_seed import seed_ch_span

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _seed_ch_trace_root(trace):
    """Give a bare PG ``trace`` a CH-only root span so the enumerated add path
    resolves it CH-native (tracer sources are read from ClickHouse only). Built in
    memory and seeded to CH — never written to PG (the tracer tables are dropped)."""
    import uuid

    span = ObservationSpan(
        id=f"chroot_{uuid.uuid4().hex[:16]}",
        project=trace.project,
        trace=trace,
        name="trace root",
        observation_type="agent",
        start_time=timezone.now() - timedelta(seconds=1),
        end_time=timezone.now(),
        status="OK",
    )
    seed_ch_span(span)  # CH only — NOT ObservationSpan.objects.create


@pytest.fixture
def observe_project(db, organization, workspace):
    return Project.objects.create(
        name="BulkAdd Observe Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def active_queue(db, organization, workspace):
    return AnnotationQueue.objects.create(
        name="Bulk Test Queue",
        organization=organization,
        workspace=workspace,
    )


def _add_items_url(queue_id):
    return f"/model-hub/annotation-queues/{queue_id}/items/add-items/"


def _api_filter(column_id, filter_type, filter_op, filter_value):
    return {
        "column_id": column_id,
        "filter_config": {
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


# --------------------------------------------------------------------------
# Backward compat — existing ``items`` payload
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddItemsEnumeratedRegression:
    def test_enumerated_happy_path(self, auth_client, active_queue, observe_project):
        t = Trace.objects.create(project=observe_project, name="t1")
        _seed_ch_trace_root(t)
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {"items": [{"source_type": "trace", "source_id": str(t.id)}]},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 1
        assert result["duplicates"] == 0
        assert result["errors"] == []

    def test_enumerated_duplicate_detection(
        self, auth_client, active_queue, observe_project
    ):
        t = Trace.objects.create(project=observe_project, name="t-dup")
        _seed_ch_trace_root(t)
        payload = {"items": [{"source_type": "trace", "source_id": str(t.id)}]}
        auth_client.post(_add_items_url(active_queue.id), payload, format="json")
        resp = auth_client.post(_add_items_url(active_queue.id), payload, format="json")
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 0
        assert result["duplicates"] == 1

    def test_enumerated_add_populates_project_id(
        self, auth_client, active_queue, observe_project
    ):
        """The denormalized project_id is stamped on add so the render/list read can
        scope its CH scan to one tenant. A NULL project_id degrades to a
        full-table scan."""
        t = Trace.objects.create(project=observe_project, name="t-proj")
        _seed_ch_trace_root(t)
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {"items": [{"source_type": "trace", "source_id": str(t.id)}]},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        item = QueueItem.objects.get(queue=active_queue, trace=t, deleted=False)
        assert str(item.project_id) == str(observe_project.id)

    def test_enumerated_scoped_with_project_id(
        self, auth_client, active_queue, observe_project
    ):
        """With the payload ``project_id``, the source resolves through the scoped batch
        read (the fast path the FE now sends) and the item is added + stamped."""
        t = Trace.objects.create(project=observe_project, name="t-scoped")
        _seed_ch_trace_root(t)
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "items": [{"source_type": "trace", "source_id": str(t.id)}],
                "project_id": str(observe_project.id),
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 1
        item = QueueItem.objects.get(queue=active_queue, trace=t, deleted=False)
        assert str(item.project_id) == str(observe_project.id)

    def test_enumerated_payload_dedupe_counts_once(
        self, auth_client, active_queue, observe_project
    ):
        """A repeated (source_type, id) in ONE payload creates a single row — the batch
        resolve-then-create path dedupes within the payload (the old per-item .exists()
        only caught ids already committed, so two copies in one request slipped through)."""
        t = Trace.objects.create(project=observe_project, name="t-dedupe")
        _seed_ch_trace_root(t)
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "items": [
                    {"source_type": "trace", "source_id": str(t.id)},
                    {"source_type": "trace", "source_id": str(t.id)},
                ],
                "project_id": str(observe_project.id),
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 1
        assert resp.data["result"]["duplicates"] == 1
        assert (
            QueueItem.objects.filter(queue=active_queue, trace=t, deleted=False).count()
            == 1
        )

    def test_enumerated_project_id_scopes_out_other_project(
        self, auth_client, active_queue, organization, workspace
    ):
        """A ``project_id`` scopes the CH read to that tenant: a trace living in another
        project can't be resolved under it (reported not-found), while the same trace
        resolves under its real project. Pins the scoped-resolution semantics."""
        proj_a = Project.objects.create(
            name="scope-proj-a",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        proj_b = Project.objects.create(
            name="scope-proj-b",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        t = Trace.objects.create(project=proj_a, name="t-in-a")
        _seed_ch_trace_root(t)

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "items": [{"source_type": "trace", "source_id": str(t.id)}],
                "project_id": str(proj_b.id),
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 0
        assert resp.data["result"]["errors"]

        resp_a = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "items": [{"source_type": "trace", "source_id": str(t.id)}],
                "project_id": str(proj_a.id),
            },
            format="json",
        )
        assert resp_a.data["result"]["added"] == 1

    def test_enumerated_add_batches_span_reads(
        self, auth_client, active_queue, observe_project
    ):
        """N collector-span items resolve in ONE batched CH read (``list_by_ids`` once
        with every id), never a point-read per item — the N+1 this change removes."""
        from unittest import mock

        from model_hub.tests.test_ch25_annotation_collector_source_resolution import (
            _CountingReaderCM,
            _make_chspan,
        )

        spans = [_make_chspan(project_id=observe_project.id) for _ in range(3)]
        reader_cm = _CountingReaderCM(spans)
        payload = {
            "items": [
                {"source_type": "observation_span", "source_id": str(s.id)}
                for s in spans
            ],
            "project_id": str(observe_project.id),
        }
        with mock.patch(
            "tracer.services.clickhouse.v2.get_reader", return_value=reader_cm
        ):
            resp = auth_client.post(
                _add_items_url(active_queue.id), payload, format="json"
            )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 3
        assert len(reader_cm.list_by_ids_calls) == 1, reader_cm.list_by_ids_calls
        assert set(reader_cm.list_by_ids_calls[0]) == {str(s.id) for s in spans}
        assert reader_cm.get_calls == []

    def test_enumerated_add_ch_failure_fails_open(
        self, auth_client, active_queue, observe_project
    ):
        """A CH read failure during resolve fails OPEN — the unresolved items surface in
        ``errors`` and the request is a clean 200, never a 500 that pins a worker."""
        import uuid
        from unittest import mock

        class _RaisingReaderCM:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def list_by_ids(self, *args, **kwargs):
                raise RuntimeError("CH unavailable")

        span_id = f"ch-span-{uuid.uuid4().hex[:12]}"
        payload = {
            "items": [{"source_type": "observation_span", "source_id": span_id}],
            "project_id": str(observe_project.id),
        }
        with mock.patch(
            "tracer.services.clickhouse.v2.get_reader",
            return_value=_RaisingReaderCM(),
        ):
            with capture_logs() as logs:
                resp = auth_client.post(
                    _add_items_url(active_queue.id), payload, format="json"
                )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 0
        assert resp.data["result"]["errors"]
        # A CH fail-open emits one canonical, alertable event carrying the
        # source_type and the caller — so an add-time outage is queryable as
        # ch_bulk_resolve_failed{caller="add_items"} without a bespoke per-name
        # query, and isn't bucketed under render errors that misdirect oncall.
        assert any(
            e["event"] == "ch_bulk_resolve_failed"
            and e.get("source_type") == "span"
            and e.get("caller") == "add_items"
            for e in logs
        )

    def test_enumerated_over_cap_returns_413(
        self, auth_client, active_queue, observe_project
    ):
        """An enumerated payload larger than the sync cap is rejected with a 413 +
        code before any resolve/insert, so an oversized add can't pin the worker
        pool on one giant IN(...) + sequential INSERT run."""
        import uuid

        import model_hub.views.annotation_queues as views_mod

        # Lower the cap for the test instead of POSTing 1001 items.
        original = views_mod.ADD_ITEMS_SYNC_MAX
        views_mod.ADD_ITEMS_SYNC_MAX = 2
        try:
            items = [
                {"source_type": "trace", "source_id": str(uuid.uuid4())}
                for _ in range(3)
            ]
            resp = auth_client.post(
                _add_items_url(active_queue.id),
                {"items": items, "project_id": str(observe_project.id)},
                format="json",
            )
        finally:
            views_mod.ADD_ITEMS_SYNC_MAX = original
        assert resp.status_code == 413, resp.data
        assert resp.data.get("code") == "items_too_large"


# --------------------------------------------------------------------------
# Filter-mode — happy + exclude + duplicates + truncation
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddItemsFilterMode:
    def test_filter_mode_no_filter_adds_all_project_traces(
        self, auth_client, active_queue, observe_project
    ):
        for i in range(3):
            _seed_ch_trace_root(
                Trace.objects.create(project=observe_project, name=f"t-{i}")
            )

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 3
        assert result["duplicates"] == 0
        assert result["errors"] == []
        assert result["total_matching"] == 3

    def test_filter_mode_add_populates_project_id(
        self, auth_client, active_queue, observe_project
    ):
        """Filter-mode add stamps project_id from the selection too, so every path
        that fills a queue leaves items scope-able by the render read."""
        _seed_ch_trace_root(Trace.objects.create(project=observe_project, name="t-fp"))
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        items = list(QueueItem.objects.filter(queue=active_queue, deleted=False))
        assert items
        assert all(str(it.project_id) == str(observe_project.id) for it in items)

    def test_filter_mode_respects_exclude_ids(
        self, auth_client, active_queue, observe_project
    ):
        traces = [
            Trace.objects.create(project=observe_project, name=f"t-{i}")
            for i in range(5)
        ]
        for _t in traces:
            _seed_ch_trace_root(_t)
        exclude = [str(traces[0].id), str(traces[1].id)]

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                    "exclude_ids": exclude,
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 3
        assert result["total_matching"] == 3

    def test_filter_mode_trace_id_filter_adds_exact_trace(
        self, auth_client, active_queue, observe_project
    ):
        target = Trace.objects.create(project=observe_project, name="target-trace")
        other = Trace.objects.create(project=observe_project, name="other-trace")
        _seed_ch_trace_root(target)
        _seed_ch_trace_root(other)

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                    "filter": [
                        _api_filter("trace_id", "text", "equals", str(target.id))
                    ],
                }
            },
            format="json",
        )

        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 1
        assert result["total_matching"] == 1
        assert QueueItem.objects.filter(trace=target, deleted=False).exists()
        assert not QueueItem.objects.filter(trace=other, deleted=False).exists()

    def test_filter_mode_counts_existing_as_duplicates(
        self, auth_client, active_queue, observe_project
    ):
        traces = [
            Trace.objects.create(project=observe_project, name=f"t-{i}")
            for i in range(3)
        ]
        for _t in traces:
            _seed_ch_trace_root(_t)
        # Pre-add one via the enumerated path.
        auth_client.post(
            _add_items_url(active_queue.id),
            {"items": [{"source_type": "trace", "source_id": str(traces[0].id)}]},
            format="json",
        )
        # Filter-add all — expect 2 fresh, 1 duplicate.
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        result = resp.data["result"]
        assert result["added"] == 2
        assert result["duplicates"] == 1
        assert result["total_matching"] == 3

    def test_filter_mode_continues_oversized_trace_selection_to_terminal_write(
        self, auth_client, active_queue, observe_project
    ):
        # Override the view-level cap for this test so we don't need to
        # seed 10_001 rows.
        import model_hub.views.annotation_queues as views_mod

        original_cap = views_mod.MAX_SELECTION_CAP
        views_mod.MAX_SELECTION_CAP = 2
        try:
            traces = []
            for i in range(3):
                trace = Trace.objects.create(project=observe_project, name=f"t-{i}")
                traces.append(trace)
                _seed_ch_trace_root(trace)
            selection = {
                "mode": "filter",
                "source_type": "trace",
                "project_id": str(observe_project.id),
            }
            first = auth_client.post(
                _add_items_url(active_queue.id),
                {"selection": selection},
                format="json",
            )
            assert first.status_code == 200, first.data
            first_result = first.data["result"]
            assert first_result["added"] == 2
            assert first_result["total_matching"] == 3
            assert first_result["total_matching_is_lower_bound"] is True
            assert first_result["has_more"] is True
            assert isinstance(first_result["next_cursor"], str)
            assert first_result["next_cursor"]

            terminal = auth_client.post(
                _add_items_url(active_queue.id),
                {
                    "selection": {
                        **selection,
                        "cursor": first_result["next_cursor"],
                    }
                },
                format="json",
            )
        finally:
            views_mod.MAX_SELECTION_CAP = original_cap

        assert terminal.status_code == 200, terminal.data
        terminal_result = terminal.data["result"]
        assert terminal_result["added"] == 1
        assert terminal_result["total_matching"] == 3
        assert terminal_result["total_matching_is_lower_bound"] is False
        assert terminal_result["has_more"] is False
        assert terminal_result["next_cursor"] is None

        queue_items = list(
            QueueItem.objects.filter(queue=active_queue, deleted=False).order_by(
                "order"
            )
        )
        assert [item.order for item in queue_items] == [1, 2, 3]
        assert {str(item.trace_id) for item in queue_items} == {
            str(trace.id) for trace in traces
        }

    def test_filter_mode_ch_failure_returns_503_not_500(
        self, auth_client, active_queue, observe_project, monkeypatch
    ):
        # The filter-mode resolvers are ClickHouse-only (no PG fallback). A CH
        # outage must surface as a structured, retryable 503 the FE can show —
        # not a raw 500 from Django's default handler.
        import model_hub.views.annotation_queues as views_mod

        def _boom(**kwargs):
            raise RuntimeError("CH down")

        monkeypatch.setitem(views_mod.FILTER_MODE_RESOLVERS, "trace", _boom)
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 503, resp.data
        assert resp.data.get("code") == "source_resolve_unavailable"

    def test_filter_mode_deadline_rolls_back_every_created_item(
        self, auth_client, active_queue, observe_project, monkeypatch
    ):
        import model_hub.views.annotation_queues as views_mod
        from model_hub.services.bulk_selection import ResolveResult
        from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

        trace = Trace.objects.create(project=observe_project, name="deadline-trace")
        seen_deadlines = []

        def resolve(**kwargs):
            seen_deadlines.append(kwargs["deadline"])
            return ResolveResult(
                ids=[str(trace.id)],
                total_matching=1,
                truncated=False,
            )

        def create_then_expire(queue, items_to_create, *, deadline=None):
            QueueItem.objects.bulk_create(items_to_create)
            raise ReadDeadlineExceeded("expired after insert")

        monkeypatch.setitem(views_mod.FILTER_MODE_RESOLVERS, "trace", resolve)
        monkeypatch.setattr(
            views_mod,
            "filter_available_source_ids_for_annotation",
            lambda *_args, **_kwargs: ([str(trace.id)], 0, None, {}),
        )
        monkeypatch.setattr(views_mod, "_finalize_bulk_add", create_then_expire)

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )

        assert resp.status_code == 503, resp.data
        assert resp.data.get("code") == "add_items_deadline_exceeded"
        assert "Nothing was added" in str(resp.data)
        assert seen_deadlines[0].total_ms == views_mod.ADD_ITEMS_FILTER_MODE_WALL_MS
        assert (
            views_mod.ADD_ITEMS_FILTER_MODE_WALL_MS
            == django_settings.INTERACTIVE_READ_DEFAULT_WALL_MS
        )
        assert not QueueItem.objects.filter(queue=active_queue, trace=trace).exists()

    def test_filter_mode_queue_item_count_matches_added(
        self, auth_client, active_queue, observe_project
    ):
        for i in range(4):
            _seed_ch_trace_root(
                Trace.objects.create(project=observe_project, name=f"t-{i}")
            )

        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 4

        # Verify via a separate GET that the queue actually holds those items.
        list_resp = auth_client.get(
            f"/model-hub/annotation-queues/{active_queue.id}/items/"
        )
        assert list_resp.status_code == 200, list_resp.data
        assert list_resp.data["count"] == 4


class TestFilterModeQueueMutationSerialization(TransactionTestCase):
    """Queue-level locking makes overlapping manager retries idempotent."""

    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.organization = Organization.objects.create(name="Queue write lock org")
        set_workspace_context(organization=self.organization)
        self.managers = [
            User.objects.create_user(
                email=f"queue-lock-manager-{index}@example.com",
                password="testpassword123",
                name=f"Queue Lock Manager {index}",
                organization=self.organization,
            )
            for index in range(2)
        ]
        self.workspace = Workspace.objects.create(
            name="Queue write lock workspace",
            organization=self.organization,
            created_by=self.managers[0],
        )
        set_workspace_context(
            workspace=self.workspace,
            organization=self.organization,
            user=self.managers[0],
        )
        self.queue = AnnotationQueue.objects.create(
            name="Queue write lock",
            organization=self.organization,
            workspace=self.workspace,
        )
        for manager in self.managers:
            AnnotationQueueAnnotator.objects.update_or_create(
                queue=self.queue,
                user=manager,
                defaults={
                    "role": AnnotatorRole.MANAGER.value,
                    "roles": FULL_ACCESS_QUEUE_ROLES,
                },
            )

    def tearDown(self):
        clear_workspace_context()
        super().tearDown()

    def test_overlapping_manager_retries_keep_unique_ids_and_orders(self):
        import model_hub.views.annotation_queues as views_mod
        from model_hub.services.bulk_selection import ResolveResult
        from tracer.services.clickhouse.read_budget import ReadDeadline

        source_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        selection = {
            "mode": "filter",
            "source_type": "trace",
            "project_id": str(uuid.uuid4()),
        }
        start_barrier = Barrier(2)
        finalize_barrier = Barrier(2)
        responses = []
        errors = []
        original_finalize = views_mod._finalize_bulk_add

        def resolve(**_kwargs):
            return ResolveResult(
                ids=source_ids,
                total_matching=len(source_ids),
                truncated=False,
            )

        def available(_source_type, resolved_ids, **_kwargs):
            return (
                list(resolved_ids),
                0,
                None,
                {
                    source_id: {"name": f"source-{index}"}
                    for index, source_id in enumerate(resolved_ids)
                },
            )

        def synchronized_finalize(queue, items_to_create, *, deadline=None):
            # Before the queue row lock, both requests build the same fresh
            # item set and meet here; concurrent inserts then race the unique
            # source constraint. With the lock, the first request times out of
            # this test-only rendezvous and commits before the second performs
            # duplicate detection, so the second has no rows to create.
            if items_to_create:
                try:
                    finalize_barrier.wait(timeout=0.5)
                except BrokenBarrierError:
                    pass
            return original_finalize(queue, items_to_create, deadline=deadline)

        def add_as(manager):
            close_old_connections()
            set_workspace_context(
                workspace=self.workspace,
                organization=self.organization,
                user=manager,
            )
            request = SimpleNamespace(
                organization=self.organization,
                workspace=self.workspace,
                user=manager,
                auth=None,
            )
            try:
                start_barrier.wait(timeout=2)
                response = views_mod.QueueItemViewSet()._add_items_filter_mode_request(
                    request,
                    self.queue.id,
                    selection,
                    deadline=ReadDeadline.start(10_000),
                )
                responses.append(response)
            except Exception as exc:  # pragma: no cover - regression diagnostic
                errors.append(exc)
            finally:
                clear_workspace_context()
                close_old_connections()

        with (
            patch.dict(views_mod.FILTER_MODE_RESOLVERS, {"trace": resolve}),
            patch.object(
                views_mod,
                "filter_available_source_ids_for_annotation",
                side_effect=available,
            ),
            patch.object(
                views_mod,
                "_finalize_bulk_add",
                side_effect=synchronized_finalize,
            ),
        ):
            threads = [
                Thread(target=add_as, args=(manager,)) for manager in self.managers
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert sorted(response.status_code for response in responses) == [200, 200]
        results = [response.data["result"] for response in responses]
        assert sorted(result["added"] for result in results) == [0, 2]
        assert sorted(result["duplicates"] for result in results) == [0, 2]

        items = list(
            QueueItem.objects.filter(queue=self.queue, deleted=False).order_by("order")
        )
        assert [item.order for item in items] == [1, 2]
        assert [str(item.trace_id) for item in items] == source_ids
        assert len({item.trace_id for item in items}) == len(source_ids)


def test_bounded_bulk_selector_receives_only_the_request_remainder(monkeypatch):
    import tracer.selectors.trace_filter_reads as trace_filter_reads
    from model_hub.services import bulk_selection

    calls = []

    class Deadline:
        def remaining_ms(self, *, floor_ms):
            calls.append(("remaining", floor_ms))
            return 4_321 if len(calls) == 1 else 4_000

    class Builder:
        @staticmethod
        def bounded_filter_degraded_error_code():
            return None

        @staticmethod
        def supports_bounded_filter_scan():
            return True

    def read_page(**kwargs):
        calls.append(("selector", kwargs["deadline_ms"]))
        return SimpleNamespace(complete=True, error_code=None, rows=[], has_more=False)

    monkeypatch.setattr(trace_filter_reads, "read_bounded_filter_page", read_page)
    page = bulk_selection._read_bounded_bulk_page(
        builder=Builder(),
        analytics=object(),
        filters=[],
        key_field="trace_id",
        cap=1,
        deadline=Deadline(),
    )

    assert page.complete is True
    assert calls == [
        ("remaining", 1),
        ("selector", 4_321),
        ("remaining", 1),
    ]


def test_filter_mode_pg_statements_receive_shrinking_timeouts(monkeypatch):
    from contextlib import contextmanager

    import model_hub.views.annotation_queues as views_mod

    remaining = iter((8_000, 7_900, 5_000, 4_900, 4_800))

    class Deadline:
        @staticmethod
        def remaining_ms(*, floor_ms):
            assert floor_ms == 1
            return next(remaining)

    class RawCursor:
        def __init__(self):
            self.timeouts = []

        def execute(self, sql, params):
            assert sql == "SELECT set_config('statement_timeout', %s, true)"
            self.timeouts.append(params[0])

    class Connection:
        vendor = "postgresql"
        wrapper = None

        @contextmanager
        def execute_wrapper(self, wrapper):
            self.wrapper = wrapper
            yield

    fake_connection = Connection()
    raw_cursor = RawCursor()
    executed = []

    def execute(sql, params, many, context):
        executed.append((sql, params, many, context))
        return sql

    monkeypatch.setattr(views_mod, "connection", fake_connection)
    context = {"cursor": SimpleNamespace(cursor=raw_cursor)}
    with views_mod._bounded_add_items_postgres(Deadline()):
        assert fake_connection.wrapper(execute, "SELECT one", (), False, context) == (
            "SELECT one"
        )
        assert fake_connection.wrapper(execute, "SELECT two", (), False, context) == (
            "SELECT two"
        )

    assert raw_cursor.timeouts == ["8000", "5000"]
    assert [row[0] for row in executed] == ["SELECT one", "SELECT two"]


def test_filter_mode_assignment_materialization_observes_shared_deadline(monkeypatch):
    import model_hub.models.annotation_queues as queue_models
    from model_hub.utils.annotation_queue_helpers import (
        assign_items_to_all_annotators,
    )
    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

    constructed = []
    writes = []

    class AssignmentManager:
        @staticmethod
        def bulk_create(assignments, **kwargs):
            writes.append((list(assignments), kwargs))

    class Assignment:
        objects = AssignmentManager()

        def __init__(self, **kwargs):
            constructed.append(kwargs)

    class Annotators:
        def filter(self, *args, **kwargs):
            return self

        def values_list(self, *args, **kwargs):
            return self

        @staticmethod
        def distinct():
            return ["annotator-1"]

    checks = 0

    def expire_during_assignment_build():
        nonlocal checks
        checks += 1
        # Three entry/query checkpoints run before materialization. The fourth
        # is the 128th assignment checkpoint and must interrupt the build.
        if checks == 4:
            raise ReadDeadlineExceeded("shared request wall exhausted")

    monkeypatch.setattr(queue_models, "QueueItemAssignment", Assignment)
    monkeypatch.setattr(queue_models, "annotation_queue_role_q", lambda *_: object())

    queue = SimpleNamespace(queue_annotators=Annotators())
    with pytest.raises(ReadDeadlineExceeded, match="shared request wall"):
        assign_items_to_all_annotators(
            queue,
            [object() for _ in range(200)],
            deadline_check=expire_during_assignment_build,
        )

    assert len(constructed) == 127
    assert writes == []


def test_filter_deadline_wraps_validation_and_invalid_shape_never_reads_db(
    monkeypatch,
):
    from rest_framework.parsers import JSONParser
    from rest_framework.request import Request
    from rest_framework.test import APIRequestFactory

    import model_hub.views.annotation_queues as views_mod
    import tfc.utils.api_contracts as api_contracts

    events = []

    class FakeReadDeadline:
        @staticmethod
        def start(total_ms):
            events.append(("deadline", total_ms))
            return SimpleNamespace(total_ms=total_ms)

    original_validate = api_contracts._validate_serializer

    def tracked_validate(*args, **kwargs):
        events.append(("validate", args[0]))
        return original_validate(*args, **kwargs)

    def unexpected_db_read(*args, **kwargs):
        raise AssertionError("invalid selection must return before queue DB access")

    monkeypatch.setattr(views_mod, "ReadDeadline", FakeReadDeadline)
    monkeypatch.setattr(api_contracts, "_validate_serializer", tracked_validate)
    monkeypatch.setattr(views_mod.AnnotationQueue.objects, "get", unexpected_db_read)

    raw_request = APIRequestFactory().post(
        "/model-hub/annotation-queues/queue-1/items/add-items/",
        {"selection": {"mode": "filter"}},
        format="json",
    )
    request = Request(raw_request, parsers=[JSONParser()])
    response = views_mod.QueueItemViewSet().add_items(request, queue_id="queue-1")

    assert response.status_code == 400
    assert events[0] == ("deadline", views_mod.ADD_ITEMS_FILTER_MODE_WALL_MS)
    assert events[1][0] == "validate"

    # The outer deadline wrapper must keep both routing and generated-contract
    # metadata copied from @action / @validated_request.
    add_items_action = views_mod.QueueItemViewSet.add_items
    assert add_items_action.mapping["post"] == "add_items"
    assert add_items_action.detail is False
    assert add_items_action.url_path == "add-items"
    assert getattr(add_items_action, "_swagger_auto_schema", None)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddItemsValidation:
    def test_both_items_and_selection_rejected(
        self, auth_client, active_queue, observe_project
    ):
        t = Trace.objects.create(project=observe_project, name="t")
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "items": [{"source_type": "trace", "source_id": str(t.id)}],
                "selection": {
                    "mode": "filter",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                },
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_neither_items_nor_selection_rejected(self, auth_client, active_queue):
        resp = auth_client.post(_add_items_url(active_queue.id), {}, format="json")
        assert resp.status_code == 400

    def test_unsupported_selection_mode(
        self, auth_client, active_queue, observe_project
    ):
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "ids",
                    "source_type": "trace",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_unsupported_source_type(self, auth_client, active_queue, observe_project):
        # All four source types (trace / observation_span / trace_session /
        # call_execution) are supported after Phase 8. This test keeps the
        # validation path covered by trying an obviously wrong value.
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "dataset_row",
                    "project_id": str(observe_project.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 400


# --------------------------------------------------------------------------
# Phase 8 — filter-mode for source_type=call_execution
#
# For call_execution, ``selection.project_id`` is reinterpreted as the
# agent_definition_id — see Phase 8 PRD.
# --------------------------------------------------------------------------


@pytest.fixture
def seeded_call_executions_for_dispatch(db, organization, workspace):
    from simulate.models.agent_definition import AgentDefinition
    from simulate.models.run_test import RunTest
    from simulate.models.scenarios import Scenarios
    from simulate.models.test_execution import CallExecution, TestExecution

    agent_def = AgentDefinition.objects.create(
        agent_name="ce-disp-agent",
        inbound=True,
        description="dispatch fixture",
        organization=organization,
        workspace=workspace,
    )
    run = RunTest.objects.create(name="ce-disp-run", organization=organization)
    te = TestExecution.objects.create(run_test=run, agent_definition=agent_def)
    scen = Scenarios.objects.create(
        name="ce-disp-scenario",
        source="dispatch",
        organization=organization,
        workspace=workspace,
    )
    ces = [
        CallExecution.objects.create(test_execution=te, scenario=scen) for _ in range(3)
    ]
    return agent_def, ces


@pytest.mark.django_db
class TestAddItemsFilterModeCallExecution:
    def test_filter_mode_ce_no_filter_adds_all(
        self, auth_client, active_queue, seeded_call_executions_for_dispatch
    ):
        agent_def, _ = seeded_call_executions_for_dispatch
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 3
        assert resp.data["result"]["total_matching"] == 3

    def test_filter_mode_ce_respects_exclude_ids(
        self, auth_client, active_queue, seeded_call_executions_for_dispatch
    ):
        agent_def, ces = seeded_call_executions_for_dispatch
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                    "exclude_ids": [str(ces[0].id)],
                }
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["result"]["added"] == 2

    def test_filter_mode_ce_continues_oversized_selection_to_terminal_write(
        self, auth_client, active_queue, seeded_call_executions_for_dispatch
    ):
        import model_hub.views.annotation_queues as views_mod

        agent_def, call_executions = seeded_call_executions_for_dispatch
        original_cap = views_mod.MAX_SELECTION_CAP
        views_mod.MAX_SELECTION_CAP = 2
        try:
            selection = {
                "mode": "filter",
                "source_type": "call_execution",
                "project_id": str(agent_def.id),
            }
            first = auth_client.post(
                _add_items_url(active_queue.id),
                {"selection": selection},
                format="json",
            )
            assert first.status_code == 200, first.data
            first_result = first.data["result"]
            assert first_result["added"] == 2
            assert first_result["total_matching"] == 3
            assert first_result["total_matching_is_lower_bound"] is True
            assert first_result["has_more"] is True
            assert isinstance(first_result["next_cursor"], str)
            assert first_result["next_cursor"]

            terminal = auth_client.post(
                _add_items_url(active_queue.id),
                {
                    "selection": {
                        **selection,
                        "cursor": first_result["next_cursor"],
                    }
                },
                format="json",
            )
        finally:
            views_mod.MAX_SELECTION_CAP = original_cap

        assert terminal.status_code == 200, terminal.data
        terminal_result = terminal.data["result"]
        assert terminal_result["added"] == 1
        assert terminal_result["total_matching"] == 3
        assert terminal_result["total_matching_is_lower_bound"] is False
        assert terminal_result["has_more"] is False
        assert terminal_result["next_cursor"] is None

        queue_items = list(
            QueueItem.objects.filter(queue=active_queue, deleted=False).order_by(
                "order"
            )
        )
        assert [item.order for item in queue_items] == [1, 2, 3]
        assert {item.call_execution_id for item in queue_items} == {
            call_execution.id for call_execution in call_executions
        }

    @pytest.mark.parametrize("filter_case", ["equals", "between"])
    def test_filter_mode_ce_continuation_preserves_created_at_boundaries(
        self,
        filter_case,
        auth_client,
        active_queue,
        seeded_call_executions_for_dispatch,
    ):
        import model_hub.views.annotation_queues as views_mod
        from simulate.models.test_execution import CallExecution

        agent_def, call_executions = seeded_call_executions_for_dispatch
        base = datetime(2020, 1, 15, tzinfo=UTC)
        if filter_case == "equals":
            created_at_values = [
                base + timedelta(hours=1),
                base + timedelta(hours=12),
                base + timedelta(hours=23),
            ]
            filter_value = (base + timedelta(hours=12)).isoformat()
        else:
            lower = base + timedelta(hours=1)
            upper = base + timedelta(hours=3)
            created_at_values = [lower, base + timedelta(hours=2), upper]
            filter_value = [lower.isoformat(), upper.isoformat()]

        for call_execution, created_at in zip(
            call_executions,
            created_at_values,
            strict=True,
        ):
            CallExecution.objects.filter(pk=call_execution.pk).update(
                created_at=created_at
            )

        selection = {
            "mode": "filter",
            "source_type": "call_execution",
            "project_id": str(agent_def.id),
            "filter": [
                _api_filter(
                    "created_at",
                    "datetime",
                    filter_case,
                    filter_value,
                )
            ],
        }
        original_cap = views_mod.MAX_SELECTION_CAP
        views_mod.MAX_SELECTION_CAP = 2
        try:
            first = auth_client.post(
                _add_items_url(active_queue.id),
                {"selection": selection},
                format="json",
            )
            assert first.status_code == 200, first.data
            first_result = first.data["result"]
            assert first_result["added"] == 2
            assert first_result["total_matching"] == 3
            assert first_result["has_more"] is True
            assert first_result["next_cursor"]

            terminal = auth_client.post(
                _add_items_url(active_queue.id),
                {
                    "selection": {
                        **selection,
                        "cursor": first_result["next_cursor"],
                    }
                },
                format="json",
            )
        finally:
            views_mod.MAX_SELECTION_CAP = original_cap

        assert terminal.status_code == 200, terminal.data
        terminal_result = terminal.data["result"]
        assert terminal_result["added"] == 1
        assert terminal_result["total_matching"] == 3
        assert terminal_result["has_more"] is False
        assert terminal_result["next_cursor"] is None

        queue_items = list(
            QueueItem.objects.filter(queue=active_queue, deleted=False).order_by(
                "order"
            )
        )
        assert [item.order for item in queue_items] == [1, 2, 3]
        assert {item.call_execution_id for item in queue_items} == {
            call_execution.id for call_execution in call_executions
        }


# --------------------------------------------------------------------------
# Manual add-items + filter-mode + non-created_at filters on call_execution.
#
# Before _apply_call_execution_filters existed, the resolver only honored
# created_at filters and silently match-all'd everything else. The fixture
# below seeds calls with mixed status/duration so we can prove status
# and duration_seconds filters now actually narrow the result.
# --------------------------------------------------------------------------


@pytest.fixture
def seeded_mixed_call_executions(db, organization, workspace):
    from model_hub.models.choices import SourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
    from simulate.models.agent_definition import AgentDefinition
    from simulate.models.run_test import RunTest
    from simulate.models.scenarios import Scenarios
    from simulate.models.test_execution import CallExecution, TestExecution

    agent_def = AgentDefinition.objects.create(
        agent_name="ce-mixed-agent",
        inbound=True,
        description="mixed-status fixture",
        organization=organization,
        workspace=workspace,
    )
    run = RunTest.objects.create(name="ce-mixed-run", organization=organization)
    te = TestExecution.objects.create(run_test=run, agent_definition=agent_def)
    dataset = Dataset.objects.create(
        name="ce-mixed-scenario-dataset",
        organization=organization,
        workspace=workspace,
    )
    priority_column = Column.objects.create(
        name="priority",
        data_type="text",
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    attempts_column = Column.objects.create(
        name="attempts",
        data_type="integer",
        dataset=dataset,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(priority_column.id), str(attempts_column.id)]
    dataset.save(update_fields=["column_order"])
    high_priority_row = Row.objects.create(dataset=dataset, order=1)
    low_priority_row = Row.objects.create(dataset=dataset, order=2)
    failed_row = Row.objects.create(dataset=dataset, order=3)
    Cell.objects.create(
        dataset=dataset,
        column=priority_column,
        row=high_priority_row,
        value="high",
    )
    Cell.objects.create(
        dataset=dataset,
        column=attempts_column,
        row=high_priority_row,
        value="2",
    )
    Cell.objects.create(
        dataset=dataset,
        column=priority_column,
        row=low_priority_row,
        value="low",
    )
    Cell.objects.create(
        dataset=dataset,
        column=attempts_column,
        row=low_priority_row,
        value="8",
    )
    Cell.objects.create(
        dataset=dataset,
        column=priority_column,
        row=failed_row,
        value="high",
    )
    Cell.objects.create(
        dataset=dataset,
        column=attempts_column,
        row=failed_row,
        value="4",
    )
    scen = Scenarios.objects.create(
        name="ce-mixed-scenario",
        source="mixed",
        organization=organization,
        workspace=workspace,
        dataset=dataset,
    )
    te.scenario_ids = [str(scen.id)]
    te.execution_metadata = {
        "Provider": True,
        "column_order": [
            {
                "id": str(priority_column.id),
                "column_name": "priority",
                "visible": True,
                "data_type": "text",
                "type": "scenario_dataset_column",
                "scenario_id": str(scen.id),
                "dataset_id": str(dataset.id),
            },
            {
                "id": str(attempts_column.id),
                "column_name": "attempts",
                "visible": True,
                "data_type": "integer",
                "type": "scenario_dataset_column",
                "scenario_id": str(scen.id),
                "dataset_id": str(dataset.id),
            },
            {
                "id": "tool_eval_accuracy",
                "column_name": "Tool Accuracy",
                "visible": True,
                "type": "tool_evaluation",
            },
        ],
    }
    te.save(update_fields=["scenario_ids", "execution_metadata"])
    completed_short = CallExecution.objects.create(
        test_execution=te,
        scenario=scen,
        status="completed",
        duration_seconds=10,
        cost_cents=12,
        row_id=high_priority_row.id,
        call_metadata={
            "row_data": {
                "persona": {
                    "name": "Casey",
                    "language": "English",
                    "languages": ["English"],
                    "communication_style": ["Direct and concise"],
                    "age_group": ["25-32"],
                    "multilingual": False,
                }
            }
        },
        tool_outputs={"tool_eval_accuracy": {"output": "pass"}},
    )
    completed_long = CallExecution.objects.create(
        test_execution=te,
        scenario=scen,
        status="completed",
        duration_seconds=120,
        customer_cost_cents=120,
        row_id=low_priority_row.id,
        call_metadata={
            "row_data": {
                "persona": {
                    "name": "Riya",
                    "language": "Hindi",
                    "languages": ["Hindi", "English"],
                    "communication_style": ["Casual and friendly"],
                    "age_group": ["32-40"],
                    "multilingual": True,
                }
            }
        },
        tool_outputs={"tool_eval_accuracy": {"output": "fail"}},
    )
    failed = CallExecution.objects.create(
        test_execution=te,
        scenario=scen,
        status="failed",
        duration_seconds=30,
        cost_cents=56,
        row_id=failed_row.id,
        call_metadata={
            "row_data": {
                "persona": {
                    "name": "Jordan",
                    "language": "English",
                    "languages": ["English"],
                    "communication_style": ["Detailed and elaborate"],
                    "age_group": ["40-50"],
                    "multilingual": False,
                }
            }
        },
        tool_outputs={"tool_eval_accuracy": {"output": "pass"}},
    )
    return (
        agent_def,
        te,
        completed_short,
        completed_long,
        failed,
        priority_column,
        attempts_column,
    )


@pytest.mark.django_db
class TestAddItemsFilterModeCallExecutionRichFilters:
    def test_simulation_add_items_grid_endpoint_applies_rules_style_filters(
        self, auth_client, seeded_mixed_call_executions
    ):
        _, test_execution, completed_short, *_ = seeded_mixed_call_executions

        resp = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/",
            {
                "filters": json.dumps(
                    [
                        _api_filter(
                            "status",
                            "categorical",
                            "equals",
                            "completed",
                        ),
                        _api_filter(
                            "duration_seconds",
                            "number",
                            "less_than",
                            60,
                        ),
                    ]
                ),
                "page": 1,
                "limit": 20,
            },
        )

        assert resp.status_code == 200, resp.data
        assert resp.data["count"] == 1
        assert [row["id"] for row in resp.data["results"]] == [str(completed_short.id)]

    def test_simulation_add_items_grid_endpoint_filters_scenario_attributes(
        self, auth_client, seeded_mixed_call_executions
    ):
        (
            _agent_def,
            test_execution,
            completed_short,
            _completed_long,
            failed,
            priority_column,
            attempts_column,
        ) = seeded_mixed_call_executions

        resp = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/",
            {
                "filters": json.dumps(
                    [
                        _api_filter(
                            priority_column.name,
                            "text",
                            "equals",
                            "high",
                        ),
                        _api_filter(
                            attempts_column.name,
                            "number",
                            "less_than",
                            5,
                        ),
                    ]
                ),
                "page": 1,
                "limit": 20,
            },
        )

        assert resp.status_code == 200, resp.data
        assert resp.data["count"] == 2
        assert {row["id"] for row in resp.data["results"]} == {
            str(completed_short.id),
            str(failed.id),
        }

    def test_simulation_add_items_grid_endpoint_filters_tool_eval_columns(
        self, auth_client, seeded_mixed_call_executions
    ):
        _, test_execution, completed_short, _completed_long, failed, *_ = (
            seeded_mixed_call_executions
        )

        resp = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/",
            {
                "filters": json.dumps(
                    [
                        _api_filter(
                            "tool_eval_accuracy",
                            "text",
                            "equals",
                            "pass",
                        )
                    ]
                ),
                "page": 1,
                "limit": 20,
            },
        )

        assert resp.status_code == 200, resp.data
        assert resp.data["count"] == 2
        assert {row["id"] for row in resp.data["results"]} == {
            str(completed_short.id),
            str(failed.id),
        }

    def test_simulation_add_items_grid_endpoint_filters_system_cost_metric(
        self, auth_client, seeded_mixed_call_executions
    ):
        _, test_execution, completed_short, *_ = seeded_mixed_call_executions

        resp = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/",
            {
                "filters": json.dumps(
                    [_api_filter("cost_cents", "number", "less_than", 20)]
                ),
                "page": 1,
                "limit": 20,
            },
        )

        assert resp.status_code == 200, resp.data
        assert resp.data["count"] == 1
        assert [row["id"] for row in resp.data["results"]] == [str(completed_short.id)]

    def test_simulation_add_items_grid_endpoint_filters_persona_fields(
        self, auth_client, seeded_mixed_call_executions
    ):
        _, test_execution, _completed_short, completed_long, *_ = (
            seeded_mixed_call_executions
        )

        resp = auth_client.get(
            f"/simulate/test-executions/{test_execution.id}/",
            {
                "filters": json.dumps(
                    [
                        _api_filter(
                            "persona.language",
                            "categorical",
                            "equals",
                            "Hindi",
                        ),
                        _api_filter(
                            "persona.multilingual",
                            "boolean",
                            "equals",
                            True,
                        ),
                    ]
                ),
                "page": 1,
                "limit": 20,
            },
        )

        assert resp.status_code == 200, resp.data
        assert resp.data["count"] == 1
        assert [row["id"] for row in resp.data["results"]] == [str(completed_long.id)]

    # ─── Filter-mode POST cases — parametrized ──────────────────────────
    # Each case: (id, filter_spec, expected_status, expected_added_or_none,
    #             expected_total_or_none, expected_message_substring_or_none)
    _FILTER_MODE_CASES = [
        (
            "status_filter_narrows_result",
            [
                {
                    "column_id": "status",
                    "filter_config": {
                        "filter_type": "categorical",
                        "filter_op": "equals",
                        "filter_value": "completed",
                    },
                }
            ],
            200,
            2,  # only 2 completed calls; NOT the failed one
            2,
            None,
        ),
        (
            "duration_range_narrows_result",
            [
                {
                    "column_id": "duration_seconds",
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "less_than",
                        "filter_value": 60,
                    },
                }
            ],
            200,
            2,  # 10s + 30s match; 120s excluded
            2,
            None,
        ),
        (
            "persona_field_narrows_result",
            [
                {
                    "column_id": "persona.communication_style",
                    "filter_config": {
                        "filter_type": "categorical",
                        "filter_op": "equals",
                        "filter_value": "Direct and concise",
                    },
                }
            ],
            200,
            1,
            1,
            None,
        ),
        (
            "unsupported_column_returns_400",
            [
                {
                    "column_id": "totally_made_up_column",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "x",
                    },
                }
            ],
            400,
            None,
            None,
            # ValueError from resolver → bad_request. Better than the old
            # silent match-all behaviour.
            "totally_made_up_column",
        ),
        (
            "combined_status_and_duration",
            [
                {
                    "column_id": "status",
                    "filter_config": {
                        "filter_type": "categorical",
                        "filter_op": "equals",
                        "filter_value": "completed",
                    },
                },
                {
                    "column_id": "duration_seconds",
                    "filter_config": {
                        "filter_type": "number",
                        "filter_op": "less_than",
                        "filter_value": 60,
                    },
                },
            ],
            200,
            1,  # only completed_short (10s, completed) matches both filters
            1,
            None,
        ),
    ]

    @pytest.mark.parametrize(
        "filter_spec,expected_status,expected_added,expected_total,error_substring",
        [case[1:] for case in _FILTER_MODE_CASES],
        ids=[case[0] for case in _FILTER_MODE_CASES],
    )
    def test_filter_mode(
        self,
        auth_client,
        active_queue,
        seeded_mixed_call_executions,
        filter_spec,
        expected_status,
        expected_added,
        expected_total,
        error_substring,
    ):
        agent_def, *_ = seeded_mixed_call_executions
        resp = auth_client.post(
            _add_items_url(active_queue.id),
            {
                "selection": {
                    "mode": "filter",
                    "source_type": "call_execution",
                    "project_id": str(agent_def.id),
                    "filter": filter_spec,
                }
            },
            format="json",
        )
        assert resp.status_code == expected_status, resp.data
        if expected_status == 200:
            assert resp.data["result"]["added"] == expected_added
            assert resp.data["result"]["total_matching"] == expected_total
        else:
            body = resp.data.get("result") or resp.data.get("message") or ""
            assert error_substring in str(body) or "cannot apply" in str(body)
