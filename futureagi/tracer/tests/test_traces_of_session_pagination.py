"""Regression test for TH-5574 — Trace View selection counts off by one.

``TraceListQueryBuilder.build()`` fetches ``page_size + 1`` rows per page as a
has-more sentinel (asserted in ``test_trace_list_ch.py``). The consuming views
must trim that sentinel back to ``page_size`` before building the response,
otherwise a page returns one extra trace — the off-by-one the user saw when
"select all on this page" reported 26 selections for a 25-row page.

This pins the trim in ``TraceView._list_traces_of_session_clickhouse`` (the
``list_traces_of_session`` endpoint named in the ticket).
"""

import uuid
from types import SimpleNamespace
from unittest import mock

import pytest


@pytest.mark.unit
class TestTracesOfSessionPagination:
    def _make_view(self):
        from tracer.views.trace import TraceView

        view = TraceView.__new__(TraceView)
        view._gm = SimpleNamespace(
            success_response=lambda payload: ("ok", payload),
            bad_request=lambda msg: ("bad_request", msg),
        )
        return view

    def _make_request(self, *, page_size):
        org = SimpleNamespace(id=uuid.uuid4())
        return SimpleNamespace(
            query_params={"page_number": "0", "page_size": str(page_size)},
            organization=org,
            user=SimpleNamespace(organization=org),
        )

    def _routing_analytics(self, *, trace_rows, total):
        """Stub ``execute_ch_query`` routing by SQL so build() runs but no CH hit.

        Phase-1 trace query returns ``trace_rows`` (page_size + 1 of them); the
        count query returns ``total``; every other auxiliary query returns [].
        """

        def _side_effect(query, params=None, **kwargs):
            q = query
            if "uniq(trace_id) AS total" in q:
                return SimpleNamespace(data=[{"total": total}])
            # Phase-1 paginated trace list (default light-column SELECT).
            if "trace_session_id" in q and "uniq(" not in q and "AS cid" not in q:
                return SimpleNamespace(data=list(trace_rows))
            return SimpleNamespace(data=[])

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _side_effect
        return analytics

    def test_page_trimmed_to_page_size(self):
        """A page that fetched page_size + 1 rows returns exactly page_size."""
        page_size = 25
        view = self._make_view()
        request = self._make_request(page_size=page_size)

        # build() asks for page_size + 1 rows for has-more detection.
        trace_rows = [{"trace_id": str(uuid.uuid4())} for _ in range(page_size + 1)]
        total = 40
        analytics = self._routing_analytics(trace_rows=trace_rows, total=total)

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
        ):
            # No eval configs for this project → discovery short-circuits with
            # candidate_ids == [] (no PG/CH eval round-trip). This test pins the
            # pagination trim, not eval columns.
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                # Pagination now comes from the serializer-validated query data
                # (request.validated_query_data), not request.query_params.
                validated_data={
                    "filters": [],
                    "page_number": 0,
                    "page_size": page_size,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        # The sentinel row must be trimmed — exactly page_size, not page_size + 1.
        assert len(payload["table"]) == page_size
        # total_rows comes from the (correct) uniq() count, unchanged by the trim.
        assert payload["metadata"]["total_rows"] == total

    def test_span_trace_map_skipped_without_annotation_labels(self):
        """No annotation labels -> the annotation map is a guaranteed no-op,
        so the span->trace map query must not run at all."""
        view = self._make_view()
        request = self._make_request(page_size=5)
        trace_rows = [{"trace_id": str(uuid.uuid4())} for _ in range(3)]
        analytics = self._routing_analytics(trace_rows=trace_rows, total=3)

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status, _ = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={"filters": [], "page_number": 0, "page_size": 5},
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        analytics.get_span_trace_map.assert_not_called()

    @pytest.mark.django_db
    def test_span_trace_map_runs_with_annotation_labels(self):
        """With labels present the span->trace map runs, scoped to project + window."""
        view = self._make_view()
        request = self._make_request(page_size=5)
        trace_rows = [{"trace_id": str(uuid.uuid4())} for _ in range(3)]
        analytics = self._routing_analytics(trace_rows=trace_rows, total=3)
        label = mock.Mock()
        label.id = uuid.uuid4()
        label.type = "text"
        label.name = "Quality"
        label.settings = {}
        project_id = str(uuid.uuid4())

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[label],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status, _ = view._list_traces_of_session_clickhouse(
                request,
                project_id=project_id,
                validated_data={"filters": [], "page_number": 0, "page_size": 5},
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        analytics.get_span_trace_map.assert_called_once()
        assert analytics.get_span_trace_map.call_args.kwargs["project_id"] == project_id

    def test_content_shortfall_logs_buffer_warning(self):
        """Fewer content rows than page traces -> the window-buffer warning fires."""
        view = self._make_view()
        request = self._make_request(page_size=5)
        trace_rows = [{"trace_id": str(uuid.uuid4())} for _ in range(3)]
        analytics = self._routing_analytics(trace_rows=trace_rows, total=3)

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
            mock.patch("tracer.views.trace.logger") as mock_logger,
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status, _ = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={"filters": [], "page_number": 0, "page_size": 5},
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        warning_events = [
            c.args[0] for c in mock_logger.warning.call_args_list if c.args
        ]
        assert (
            "trace content enrichment returned fewer traces than requested"
            in warning_events
        )
